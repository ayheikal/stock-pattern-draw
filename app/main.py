from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data import VALID_INTERVALS, VALID_PERIODS, InsufficientHistory, SymbolNotFound, fetch_ohlcv
from . import backtest, profile
from .outlook import build_outlook
from .patterns import analyze
from .screener import MAX_SYMBOLS, UNIVERSES, scan

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Pattern Charts", version="1.0")


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
    }


@app.get("/api/universes")
def api_universes():
    return {k: {"label": u["label"], "count": len(u["symbols"])} for k, u in UNIVERSES.items()}


@app.get("/api/screen")
def api_screen(
    universe: str = Query("egx"),
    symbols: str = Query("", max_length=2000, description="Comma-separated list; overrides universe"),
    interval: str = Query("1d"),
    period: str = Query("1y"),
    min_score: float = Query(0.4, description="0.4 = mildly bullish, 1.2 = bullish"),
):
    if interval not in VALID_INTERVALS or period not in VALID_PERIODS:
        raise HTTPException(400, "invalid interval or period")
    if symbols.strip():
        syms = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    elif universe in UNIVERSES:
        syms = UNIVERSES[universe]["symbols"]
    else:
        raise HTTPException(400, f"universe must be one of {sorted(UNIVERSES)}")
    if len(syms) > MAX_SYMBOLS:
        raise HTTPException(400, f"at most {MAX_SYMBOLS} symbols per scan")
    return scan(syms, interval, period, min_score)


@app.get("/api/profile")
def api_profile(interval: str = Query("1d"), live: bool = Query(True)):
    """My entries; with live=true each is compared against the current chart outlook."""
    if interval not in ("1d", "1wk"):
        raise HTTPException(400, "interval must be 1d or 1wk")
    if not live:
        return profile.load().model_dump()
    return profile.live_profile(interval, "5y" if interval == "1wk" else "1y")


@app.put("/api/profile/name")
def api_profile_name(body: dict):
    return profile.set_name(str(body.get("name", ""))[:60]).model_dump()


@app.put("/api/profile/settings")
def api_profile_settings(settings: profile.Settings):
    return profile.set_settings(settings).model_dump()


@app.get("/api/size")
def api_size(entry: float = Query(..., gt=0), stop: float = Query(..., gt=0), currency: str = Query("")):
    """Shares to buy at `entry` with `stop`, under the rules saved in my profile."""
    return profile.sizing_for(profile.load().settings, currency or None, entry, stop)


@app.put("/api/profile/entries")
def api_profile_upsert(entry: profile.Entry):
    symbols = {e.symbol for e in profile.load().entries}
    if entry.symbol not in symbols and len(symbols) >= MAX_SYMBOLS:
        raise HTTPException(400, f"at most {MAX_SYMBOLS} entries")
    return profile.upsert(entry).model_dump()


@app.delete("/api/profile/entries/{symbol}")
def api_profile_delete(symbol: str):
    if not profile.remove(symbol):
        raise HTTPException(404, f"'{symbol}' is not in your profile")
    return {"ok": True}


def _backtest_args(universe: str, interval: str) -> None:
    if universe not in UNIVERSES:
        raise HTTPException(400, f"universe must be one of {sorted(UNIVERSES)}")
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
    return {"started": backtest.start(universe, interval)}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/screener")
def screener_page():
    return FileResponse(STATIC / "screener.html")


@app.get("/profile")
def profile_page():
    return FileResponse(STATIC / "profile.html")


@app.get("/track")
def track_page():
    return FileResponse(STATIC / "track.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
