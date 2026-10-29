from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import os
import secrets
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .data import VALID_INTERVALS, VALID_PERIODS, InsufficientHistory, SymbolNotFound, fetch_ohlcv
from . import access, alerts, backtest, decision, profile, shariah
from .outlook import build_outlook
from .patterns import analyze
from .screener import MAX_SYMBOLS, SHOW, _analyze_one, adtv, all_universes, get_universe, rank, scan

STATIC = Path(__file__).resolve().parent.parent / "static"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    alerts.start_background()  # Telegram checks during EGX hours (off unless enabled in My profile)
    yield


app = FastAPI(title="Market Lens", version="1.0", lifespan=lifespan)


@app.middleware("http")
async def access_guard(request: Request, call_next):
    """In phone mode, devices other than this computer need the access key (see app/access.py)."""
    key = access.get_key()
    path = request.url.path
    given = request.query_params.get("key")
    if key and given and secrets.compare_digest(given, key):
        # Remember this device, and take the key out of the address bar.
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
        resp = RedirectResponse(path + ("?" + urlencode(rest) if rest else ""), status_code=303)
        resp.set_cookie(access.COOKIE, key, max_age=365 * 86400, httponly=True, samesite="lax")
        return resp
    client = request.client.host if request.client else ""
    if not access.allowed(client, path, request.cookies.get(access.COOKIE), key):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Access key required"}, status_code=401)
        return HTMLResponse(access.LOGIN_PAGE, status_code=401)
    return await call_next(request)


def _times(index: pd.DatetimeIndex, interval: str) -> list:
    """Chart times: 'YYYY-MM-DD' for daily+ bars, exchange wall-clock epoch seconds intraday."""
    if interval in ("1d", "1wk", "1mo"):
        return [ts.strftime("%Y-%m-%d") for ts in index]
    naive = index.tz_localize(None) if index.tz is not None else index
    return [int(ts.timestamp()) for ts in naive.tz_localize("UTC")]


def _r(x: float) -> float:
    return round(float(x), 4)


SMA_LENGTH = 200
# A longer period to fetch so the SMA is defined from the first visible bar.
WARMUP_PERIOD = {"3mo": "2y", "6mo": "2y", "1y": "2y", "2y": "5y", "5y": "10y", "10y": "max", "max": "max"}


def _sma(resolved: str, df: pd.DataFrame, interval: str, period: str) -> pd.Series:
    """Simple moving average of closes over SMA_LENGTH bars, aligned to df's index."""
    closes = df["Close"]
    if WARMUP_PERIOD[period] != period:
        try:
            _, longer, _ = fetch_ohlcv(resolved, interval, WARMUP_PERIOD[period])
            closes = longer["Close"].combine_first(closes)
        except (SymbolNotFound, InsufficientHistory):
            pass
    return closes.rolling(SMA_LENGTH).mean().reindex(df.index)


@app.get("/api/analyze")
def api_analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    interval: str = Query("1d"),
    period: str = Query("1y"),
):
    if interval not in VALID_INTERVALS:
        raise HTTPException(400, f"interval must be one of {sorted(VALID_INTERVALS)}")
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    try:
        resolved, df, meta = fetch_ohlcv(symbol, interval, period)
    except InsufficientHistory as e:
        raise HTTPException(
            422,
            f"'{e.symbol}' was found, but only {e.bars} bar(s) of history are available, "
            "which is not enough to analyze. Try a longer period or a daily interval.",
        )
    except SymbolNotFound:
        raise HTTPException(
            404,
            f"No data for '{symbol}'. Use Yahoo Finance codes (AAPL, ^GSPC, BTC-USD, COMI.CA), "
            "EGX index names (EGX30, EGX33, EGX70) or TradingView codes (EGX:SHARIAH).",
        )

    times = _times(df.index, interval)
    sma = _sma(resolved, df, interval, period)
    patterns, levels = analyze(df)

    def t(i: int):
        return times[max(0, min(len(times) - 1, int(i)))]

    candles = [
        {"time": times[i], "open": _r(r.Open), "high": _r(r.High), "low": _r(r.Low), "close": _r(r.Close), "volume": float(r.Volume)}
        for i, r in enumerate(df.itertuples())
    ]
    out_patterns = []
    for k, p in enumerate(patterns):
        d = asdict(p)
        d["id"] = k
        d["startTime"], d["endTime"] = t(p.start), t(p.end)
        d["points"] = [{"time": t(i), "index": int(i), "value": _r(v)} for i, v in p.points]
        d["lines"] = [[{"time": t(i), "index": int(i), "value": _r(v)} for i, v in ln] for ln in p.lines]
        if p.neckline:
            d["neckline"] = [{"time": t(i), "index": int(i), "value": _r(v)} for i, v in p.neckline]
        out_patterns.append(d)

    return {
        "symbol": resolved,
        "name": meta.get("longName") or meta.get("shortName") or resolved,
        "currency": meta.get("currency"),
        "adtv": adtv(df, interval, meta.get("currency")),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "interval": interval,
        "period": period,
        "candles": candles,
        "sma": {
            "length": SMA_LENGTH,
            "points": [{"time": times[i], "value": _r(v)} for i, v in enumerate(sma) if pd.notna(v)],
        },
        "patterns": out_patterns,
        "levels": [asdict(lv) | {"firstTime": t(lv.first), "lastTime": t(lv.last)} for lv in levels],
        "outlook": build_outlook(df, patterns, levels, interval, float(sma.iloc[-1]) if pd.notna(sma.iloc[-1]) else None),
        "halal": shariah.status(profile.load().shariah, resolved) if meta.get("currency") else None,
    }


@app.get("/api/universes")
def api_universes():
    return {k: {"label": u["label"], "count": len(u["symbols"])} for k, u in all_universes().items()}


@app.get("/api/screen")
def api_screen(
    universe: str = Query("egx"),
    symbols: str = Query("", max_length=2000, description="Comma-separated list; overrides universe"),
    interval: str = Query("1d"),
    period: str = Query("1y"),
    min_score: float = Query(-99, description="Optional outlook filter (0.4 = mildly bullish); off by default, the chart has no proven edge"),
    min_adtv: float = Query(0, ge=0, description="Skip stocks trading less than this value a day"),
    halal_only: bool = Query(False, description="Only stocks on my halal list or tagged halal"),
    show: str = Query("not-avoid", description="ready = Ready to buy only, not-avoid = all but Avoid, all = every verdict"),
):
    """Scan a watchlist and rank it by the buy checklist (Ready, then Wait, then the rest)."""
    if interval not in VALID_INTERVALS or period not in VALID_PERIODS:
        raise HTTPException(400, "invalid interval or period")
    if show not in SHOW:
        raise HTTPException(400, f"show must be one of {list(SHOW)}")
    if symbols.strip():
        syms = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    elif get_universe(universe):
        syms = get_universe(universe)["symbols"]
    else:
        raise HTTPException(400, f"unknown universe '{universe}'")
    if len(syms) > MAX_SYMBOLS:
        raise HTTPException(400, f"at most {MAX_SYMBOLS} symbols per scan")
    out = scan(syms, interval, period, min_score, min_adtv)
    prof = profile.load()
    ctx = decision.Context(prof)
    ctx.prefetch(r["symbol"] for r in out["results"] if r.get("currency"))
    for r in out["results"]:
        r["halal"] = shariah.status(prof.shariah, r["symbol"]) if r.get("currency") else None
        r["decision"] = ctx.evaluate(r, interval)
        r["nextEarnings"] = r["decision"].get("nextEarnings")
    if halal_only:
        out["results"] = [r for r in out["results"] if (r["halal"] or {}).get("status") == "halal"]
    ranked = rank(out["results"], show)
    out["hidden"] = {"avoid": sum(1 for r in out["results"] if r["decision"]["verdict"] == "avoid" and r not in ranked),
                     "other": sum(1 for r in out["results"] if r["decision"]["verdict"] != "avoid" and r not in ranked)}
    out["results"] = ranked
    return out


@app.get("/api/decision")
def api_decision(symbol: str = Query(..., min_length=1, max_length=32), interval: str = Query("1d")):
    """The buy checklist for one symbol: each check, the verdict, and how much my rules allow."""
    if interval not in ("1d", "1wk"):
        raise HTTPException(400, "interval must be 1d or 1wk")
    try:
        row = _analyze_one(symbol, interval, "5y" if interval == "1wk" else "1y")
    except (SymbolNotFound, InsufficientHistory):
        raise HTTPException(404, f"No data for '{symbol}'.")
    prof = profile.load()
    ctx = decision.Context(prof)
    entry = ctx.entry(row)
    return {"symbol": row["symbol"], "position": entry.position if entry else None,
            "sector": (ctx.fund(row["symbol"]) or {}).get("sector")} | ctx.evaluate(row, interval, entry=entry)


@app.get("/api/profile")
def api_profile(interval: str = Query("1d"), live: bool = Query(True)):
    """My entries; with live=true each is compared against the current chart outlook."""
    if interval not in ("1d", "1wk"):
        raise HTTPException(400, "interval must be 1d or 1wk")
    if not live:
        return profile.public(profile.load())
    return profile.live_profile(interval, "5y" if interval == "1wk" else "1y")


@app.put("/api/profile/name")
def api_profile_name(body: dict):
    return profile.public(profile.set_name(str(body.get("name", ""))[:60]))


@app.put("/api/profile/settings")
def api_profile_settings(settings: profile.Settings):
    return profile.public(profile.set_settings(settings))


@app.get("/api/backups")
def api_backups():
    return profile.backups()


@app.post("/api/backups/restore")
def api_backup_restore(name: str = Query(..., max_length=80)):
    try:
        return profile.public(profile.restore_backup(name))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/profile/export")
def api_profile_export():
    """My profile as a file to keep (without the Telegram token)."""
    from datetime import date as _date

    from fastapi.responses import JSONResponse
    return JSONResponse(profile.public(profile.load()),
                        headers={"Content-Disposition": f'attachment; filename="market-lens-profile-{_date.today()}.json"'})


@app.get("/api/size")
def api_size(entry: float = Query(..., gt=0), stop: float = Query(..., gt=0), currency: str = Query(""),
             adtv: float | None = Query(None, gt=0)):
    """Shares to buy at `entry` with `stop`, under the rules saved in my profile."""
    return profile.sizing_for(profile.load().settings, currency or None, entry, stop, adtv)


@app.put("/api/profile/entries")
def api_profile_upsert(entry: profile.Entry):
    symbols = {e.symbol for e in profile.load().entries}
    if entry.symbol not in symbols and len(symbols) >= MAX_SYMBOLS:
        raise HTTPException(400, f"at most {MAX_SYMBOLS} entries")
    return profile.public(profile.upsert(entry))


@app.post("/api/profile/entries/{symbol}/trades")
def api_add_trade(symbol: str, trade: profile.Trade):
    prof = profile.add_trade(symbol, trade)
    if prof is None:
        raise HTTPException(404, f"'{symbol}' is not in your profile")
    return profile.public(prof)


@app.delete("/api/profile/entries/{symbol}/trades/{index}")
def api_remove_trade(symbol: str, index: int):
    prof = profile.remove_trade(symbol, index)
    if prof is None:
        raise HTTPException(404, "No such trade")
    return profile.public(prof)


@app.delete("/api/profile/entries/{symbol}")
def api_profile_delete(symbol: str):
    if not profile.remove(symbol):
        raise HTTPException(404, f"'{symbol}' is not in your profile")
    return {"ok": True}


def _backtest_args(universe: str, interval: str) -> None:
    if not get_universe(universe):
        raise HTTPException(400, f"unknown universe '{universe}'")
    if interval not in backtest.HISTORY:
        raise HTTPException(400, f"interval must be one of {sorted(backtest.HISTORY)}")


@app.get("/api/backtest")
def api_backtest(universe: str = Query("egx"), interval: str = Query("1d"), range: str = Query("all")):
    """Cached track record over a look-back period, plus whether a run is in progress."""
    _backtest_args(universe, interval)
    if range not in backtest.RANGES:
        raise HTTPException(400, f"range must be one of {list(backtest.RANGES)}")
    result = backtest.load(universe, interval)
    return {"status": backtest.status(universe, interval), "result": result and backtest.view(result, range)}


@app.post("/api/backtest/run")
def api_backtest_run(universe: str = Query("egx"), interval: str = Query("1d")):
    _backtest_args(universe, interval)
    rules = profile.load().settings
    return {"started": backtest.start(universe, interval, rules.feePct, rules.minAdtv)}


# Cards at the top of the market page: the main indices, plus the dollar rate every EGX price is priced against.
INDICES = [("EGX30", "EGX 30"), ("EGX70", "EGX 70 EWI"), ("EGX100", "EGX 100 EWI"), ("EGX33", "EGX 33 Shariah"),
           ("USDEGP", "USD / EGP")]


def _index_card(code: str, label: str) -> dict:
    try:
        _, df, _ = fetch_ohlcv(code, "1d", "2y")
    except (SymbolNotFound, InsufficientHistory):
        return {"symbol": code, "name": label, "error": "no data"}
    c = df["Close"]
    last = float(c.iloc[-1])
    ch = lambda n: round((last / float(c.iloc[-1 - n]) - 1) * 100, 2) if len(c) > n else None
    year = c[c.index.year == c.index[-1].year]
    sma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None
    sma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    if sma50 and sma200:
        trend = "Uptrend" if last > sma50 > sma200 else "Downtrend" if last < sma50 < sma200 else "Mixed"
    else:
        trend = "Too little history"
    return {"symbol": code, "name": label, "last": round(last, 2), "day": ch(1), "week": ch(5), "month": ch(21),
            "ytd": round((last / float(year.iloc[0]) - 1) * 100, 2) if len(year) > 1 else None,
            "above50": None if sma50 is None else last > sma50, "above200": None if sma200 is None else last > sma200,
            "trend": trend, "date": df.index[-1].strftime("%Y-%m-%d"),
            "spark": [round(float(v), 2) for v in c.tail(60)]}


@app.get("/api/market")
def api_market():
    """EGX at a glance: main indices, breadth, sectors and movers."""
    from concurrent.futures import ThreadPoolExecutor

    from .egx import fetch_market, overview
    try:
        market = fetch_market()
    except Exception as e:
        raise HTTPException(502, f"Couldn't load the EGX market snapshot: {str(e)[:120]}")
    with ThreadPoolExecutor(max_workers=len(INDICES)) as pool:
        indices = list(pool.map(lambda x: _index_card(*x), INDICES))
    out = overview(market, indices)
    sh = profile.load().shariah
    for k in ("gainers", "losers", "active"):
        for r in out[k]:
            r["halal"] = shariah.status(sh, r["symbol"])
    return out


def _shariah_payload(prof: profile.Profile, **extra) -> dict:
    """My halal list with company names (when the market snapshot loads) and my own tags."""
    from datetime import date as _date

    from .egx import fetch_market
    try:
        names = {s["symbol"]: s["name"] for s in fetch_market()["stocks"]}
    except Exception:
        names = {}
    sh = prof.shariah
    return {"symbols": [{"symbol": s, "name": names.get(s, "")} for s in sh.symbols],
            "listDate": sh.listDate and sh.listDate.isoformat(), "stale": shariah.is_stale(sh.listDate, _date.today()),
            "tags": [{"symbol": s, "name": names.get(s, ""), "status": v} for s, v in sorted(sh.tags.items())]} | extra


@app.get("/api/shariah")
def api_shariah():
    return _shariah_payload(profile.load())


@app.put("/api/shariah/list")
def api_shariah_list(body: dict):
    """Replace the EGX 33 Shariah list with codes pasted from EGX (a list, or a copied table); empty clears it."""
    from datetime import date as _date

    from .egx import fetch_market
    text = str(body.get("text") or "")[:50_000]
    try:
        list_date = _date.fromisoformat(body["listDate"]) if body.get("listDate") else _date.today()
    except ValueError:
        raise HTTPException(400, "listDate must be YYYY-MM-DD")
    try:
        market = fetch_market()["stocks"]
    except Exception:
        market = None  # offline: take the codes as typed
    symbols, unknown = shariah.parse_list(text, market)
    if text.strip() and not symbols:
        raise HTTPException(400, "No EGX stock codes found. Paste codes like COMI.CA, or copy the table from EGX's website.")
    return _shariah_payload(profile.set_shariah_list(symbols, list_date), unknown=unknown, checked=market is not None)


@app.put("/api/shariah/tag")
def api_shariah_tag(body: dict):
    """Tag one stock halal / not halal myself ({"symbol", "status": "halal" | "not" | null})."""
    symbol, status = str(body.get("symbol") or "").strip(), body.get("status")
    if not symbol or len(symbol) > 32:
        raise HTTPException(400, "symbol is required")
    if status not in ("halal", "not", None):
        raise HTTPException(400, "status must be halal, not or null")
    prof = profile.set_halal_tag(symbol, status)
    return _shariah_payload(prof, halal=shariah.status(prof.shariah, symbol))


def _sectors_payload(prof: profile.Profile) -> dict:
    from datetime import date as _date

    from .egx import fetch_fundamentals, sector_outlook
    try:
        funds = fetch_fundamentals()
    except Exception as e:
        raise HTTPException(502, f"Couldn't load EGX company financials: {str(e)[:120]}")
    return {"asOf": funds["asOf"]} | sector_outlook(funds["stocks"], prof.inflationPct, prof.sectors, _date.today())


@app.get("/api/sectors")
def api_sectors():
    """Growth / Normal / Shallow per EGX sector from company financials, merged with my own views and events."""
    return _sectors_payload(profile.load())


@app.put("/api/sectors/settings/inflation")
def api_sectors_inflation(body: dict):
    try:
        pct = float(body.get("inflationPct"))
    except (TypeError, ValueError):
        raise HTTPException(400, "inflationPct must be a number")
    if not 0 <= pct <= 100:
        raise HTTPException(400, "inflationPct must be between 0 and 100")
    return _sectors_payload(profile.set_inflation(pct))


@app.put("/api/sectors/{slug}")
def api_sector_view(slug: str, view: profile.SectorView):
    if not slug or len(slug) > 60:
        raise HTTPException(400, "bad sector")
    return _sectors_payload(profile.set_sector(slug, view))


@app.get("/api/alerts/status")
def api_alerts_status():
    s = profile.load().settings
    st = alerts.load_state()
    return {"on": s.alertsOn, "configured": bool(s.telegramToken and s.telegramChatId),
            "inSession": alerts.in_session(alerts.cairo_now(), s.holidays),
            "lastCheck": st.get("lastCheck"), "lastSent": st.get("lastSent"), "lastError": st.get("lastError"),
            "active": len(st.get("active", []))}


@app.post("/api/alerts/test")
def api_alerts_test():
    try:
        alerts.send_test()
    except Exception as e:
        raise HTTPException(400, str(e)[:200])
    return {"ok": True}


@app.post("/api/alerts/check")
def api_alerts_check():
    """Check now, whatever the time (the background loop only runs in EGX hours)."""
    return alerts.check_once()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/screener")
def screener_page():
    return FileResponse(STATIC / "screener.html")


@app.get("/profile")
def profile_page():
    return FileResponse(STATIC / "profile.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # served from the root so it can look after every page; never cached, so updates arrive
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/api/phone")
def api_phone(request: Request):
    """The link (with access key) a phone opens to log in; only shown on this computer."""
    if (request.client.host if request.client else "") not in access.LOCAL:
        raise HTTPException(403, "Only available on the computer running the app")
    return access.phone_link(int(os.environ.get("PATTERN_PORT", "8000")))


@app.get("/market")
def market_page():
    return FileResponse(STATIC / "market.html")


@app.get("/track")
def track_page():
    return FileResponse(STATIC / "track.html")


@app.get("/sectors")
def sectors_page():
    return FileResponse(STATIC / "sectors.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
