"""The whole Egyptian Exchange in one request: every listed stock with sector, price, change and liquidity.

Source: TradingView's screener endpoint (unofficial, like the index feed in tradingview.py;
fine for personal use, see README). Cached for a few minutes. Data is delayed.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import httpx
from cachetools import TTLCache

SCAN_URL = "https://scanner.tradingview.com/egypt/scan"
COLUMNS = ["name", "description", "sector", "close", "change", "volume", "Value.Traded", "market_cap_basic", "SMA50", "SMA200",
           "isin"]
_cache: TTLCache = TTLCache(maxsize=4, ttl=300)
_lock = threading.Lock()


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def fetch_market() -> dict:
    """{"asOf": epoch seconds, "stocks": [...]} for every EGX-listed stock, most traded first."""
    with _lock:
        if "market" in _cache:
            return _cache["market"]
    body = {"filter": [{"left": "type", "operation": "equal", "right": "stock"}], "columns": COLUMNS,
            "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"}, "range": [0, 1000]}
    r = httpx.post(SCAN_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    stocks = []
    for row in r.json().get("data", []):
        d = dict(zip(COLUMNS, row["d"]))
        stocks.append({
            "symbol": f"{d['name']}.CA",  # the app's code for an EGX stock (Yahoo style)
            "name": d["description"] or d["name"],
            "sector": d["sector"] or "Other",
            "close": _num(d["close"]),
            "changePct": round(_num(d["change"]) or 0.0, 2),
            "volume": _num(d["volume"]) or 0.0,
            "value": _num(d["Value.Traded"]) or 0.0,  # EGP traded in the latest session
            "marketCap": _num(d["market_cap_basic"]),
            "sma50": _num(d["SMA50"]),
            "sma200": _num(d["SMA200"]),
            "isin": d["isin"] or None,  # matches the codes in EGX's own index files
        })
    out = {"asOf": int(time.time()), "stocks": stocks}
    with _lock:
        _cache["market"] = out
    return out


def slug(sector: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sector.lower()).strip("-")


def universes(max_symbols: int) -> dict[str, dict]:
    """Watchlists built from the live market: the most traded stocks, and one per sector."""
    stocks = [s for s in fetch_market()["stocks"] if s["value"] > 0]  # skip suspended / untraded
    out = {"egx_top": {"label": f"EGX: {min(max_symbols, len(stocks))} most traded stocks",
                       "symbols": [s["symbol"] for s in stocks[:max_symbols]]}}
    by_sector: dict[str, list[str]] = {}
    for s in stocks:
        by_sector.setdefault(s["sector"], []).append(s["symbol"])
    for sector, syms in sorted(by_sector.items(), key=lambda kv: -len(kv[1])):
        out[f"egx_sector_{slug(sector)}"] = {"label": f"EGX sector: {sector}", "symbols": syms[:max_symbols]}
    return out


def overview(market: dict, indices: list[dict]) -> dict:
    """Breadth, sectors and movers from the market snapshot; `indices` are passed in ready-made."""
    traded = [s for s in market["stocks"] if s["value"] > 0 and s["close"]]
    adv = sum(1 for s in traded if s["changePct"] > 0)
    dec = sum(1 for s in traded if s["changePct"] < 0)
    above = lambda k: [s for s in traded if s[k]]
    pct_above = lambda k: round(sum(1 for s in above(k) if s["close"] > s[k]) / len(above(k)) * 100, 1) if above(k) else None

    sectors: dict[str, dict] = {}
    for s in traded:
        g = sectors.setdefault(s["sector"], {"sector": s["sector"], "count": 0, "value": 0.0, "_w": 0.0, "_wc": 0.0, "adv": 0, "dec": 0})
        g["count"] += 1
        g["value"] += s["value"]
        w = s["marketCap"] or 0.0
        g["_w"] += w
        g["_wc"] += w * s["changePct"]
        g["adv"] += s["changePct"] > 0
        g["dec"] += s["changePct"] < 0
    sector_rows = []
    for g in sectors.values():
        mean = sum(s["changePct"] for s in traded if s["sector"] == g["sector"]) / g["count"]
        sector_rows.append({"sector": g["sector"], "slug": slug(g["sector"]), "count": g["count"], "value": round(g["value"]),
                            "adv": g["adv"], "dec": g["dec"],
                            "changePct": round(g["_wc"] / g["_w"], 2) if g["_w"] else round(mean, 2)})
    sector_rows.sort(key=lambda g: -g["value"])

    liquid = [s for s in traded if s["value"] >= 1_000_000]  # ignore tiny prints when ranking movers
    pick = lambda xs: [{k: s[k] for k in ("symbol", "name", "sector", "close", "changePct", "value")} for s in xs[:8]]
    return {
        "asOf": market["asOf"],
        "indices": indices,
        "breadth": {"traded": len(traded), "advancers": adv, "decliners": dec, "unchanged": len(traded) - adv - dec,
                    "above50": pct_above("sma50"), "above200": pct_above("sma200"),
                    "value": round(sum(s["value"] for s in traded))},
        "sectors": sector_rows,
        "gainers": pick(sorted(liquid, key=lambda s: -s["changePct"])),
        "losers": pick(sorted(liquid, key=lambda s: s["changePct"])),
        "active": pick(traded),  # already sorted by value traded
    }


# --- Sector outlook: company financials and upcoming events, then a conclusion that adds the price trend ----

FUND_COLUMNS = ["name", "description", "sector", "industry", "market_cap_basic",
                "total_revenue_yoy_growth_ttm", "total_revenue_qoq_growth_fq",
                "earnings_per_share_diluted_yoy_growth_ttm", "earnings_per_share_diluted_qoq_growth_fq",
                "return_on_equity", "net_margin", "price_earnings_ttm", "dividends_yield_current",
                "earnings_release_next_date", "Perf.1M", "Perf.3M", "Perf.Y", "close", "SMA50", "SMA200", "RSI",
                # for the fundamental scorecard (rating.py); many EGX companies leave some of these blank
                "price_book_fq", "enterprise_value_ebitda_ttm", "return_on_invested_capital", "operating_margin_ttm",
                "debt_to_equity_fq", "net_debt_fq", "ebitda_ttm", "current_ratio_fq", "quick_ratio_fq",
                "cash_f_operating_activities_ttm", "net_income_ttm", "free_cash_flow_margin_ttm", "price_free_cash_flow_ttm",
                "dividend_payout_ratio_ttm", "Value.Traded"]
REAL_GROWTH_BAND = 5.0   # real (after-inflation) median growth beyond ±this counts for or against a sector
GROWING_SHARE_HI = 60    # at least this % of companies with real earnings growth counts for a sector...
GROWING_SHARE_LO = 40    # ...and at most this % counts against it
MIN_COMPANIES = 3        # fewer companies with growth figures than this: not enough to judge
EVENT_DAYS = 90          # earnings dates this close are listed as upcoming events
STATUS_ORDER = {"growth": 0, "normal": 1, "shallow": 2, "unknown": 3}
# Technical side: share of stocks above their 50/200-day averages and the sector's 3-month move
ABOVE_HI, ABOVE_LO = 60, 40  # % of stocks above an average that counts for / against the trend
MOMENTUM_BAND = 5.0          # cap-weighted 3-month move beyond ±this % counts for / against
RSI_HOT, RSI_COLD = 70, 30   # median RSI beyond these: overbought / oversold
# Conclusion from (financial status, technical trend): verdict key, label, what it means
VERDICTS = {
    ("growth", "up"): ("favour", "Favour", "Strong financials and a rising trend agree."),
    ("growth", "mixed"): ("accumulate", "Accumulate", "Good financials, but price hasn't confirmed yet. Build positions gradually or wait for a breakout."),
    ("growth", "down"): ("watch", "Watch for a turn", "Good financials, but prices are falling. A possible opportunity once the downtrend ends; don't catch a falling knife."),
    ("normal", "up"): ("accumulate", "Ride the trend", "Price is running ahead of average financials. Fine to ride with a stop, not a long-term case."),
    ("normal", "mixed"): ("neutral", "Neutral", "Average financials and no clear trend. No edge either way."),
    ("normal", "down"): ("caution", "Caution", "Average financials and a falling trend. Wait for the trend to improve."),
    ("shallow", "up"): ("caution", "Caution", "Price is rising without financial support. Take profits or keep stops tight."),
    ("shallow", "mixed"): ("caution", "Caution", "Weak financials and no trend to lean on."),
    ("shallow", "down"): ("avoid", "Avoid", "Weak financials and a falling trend agree."),
    ("unknown", "up"): ("watch", "Trend only", "Too few financial figures; the trend is up. Treat as a trading idea, not an investment case."),
    ("unknown", "mixed"): ("neutral", "Neutral", "Too few financial figures and no clear trend."),
    ("unknown", "down"): ("caution", "Caution", "Too few financial figures and a falling trend."),
    ("growth", "unknown"): ("accumulate", "Accumulate", "Good financials; too little price history to judge the trend."),
    ("normal", "unknown"): ("neutral", "Neutral", "Average financials; too little price history to judge the trend."),
    ("shallow", "unknown"): ("caution", "Caution", "Weak financials; too little price history to judge the trend."),
    ("unknown", "unknown"): ("neutral", "Not enough data", "Too few financial figures and too little price history."),
}
VERDICT_ORDER = {"favour": 0, "accumulate": 1, "watch": 2, "neutral": 3, "caution": 4, "avoid": 5}
TREND_LABEL = {"up": "Uptrend", "mixed": "Mixed", "down": "Downtrend", "unknown": "Not enough data"}


def fetch_fundamentals() -> dict:
    """{"asOf": epoch seconds, "stocks": [...]}: growth, profitability, valuation and next earnings date per stock."""
    with _lock:
        if "fundamentals" in _cache:
            return _cache["fundamentals"]
    body = {"filter": [{"left": "type", "operation": "equal", "right": "stock"}], "columns": FUND_COLUMNS,
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 1000]}
    r = httpx.post(SCAN_URL, json=body, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    stocks = []
    for row in r.json().get("data", []):
        d = dict(zip(FUND_COLUMNS, row["d"]))
        yoy_rev, yoy_eps = _num(d["total_revenue_yoy_growth_ttm"]), _num(d["earnings_per_share_diluted_yoy_growth_ttm"])
        stocks.append({
            "symbol": f"{d['name']}.CA",
            "name": d["description"] or d["name"],
            "sector": d["sector"],
            "industry": d["industry"],
            "marketCap": _num(d["market_cap_basic"]),
            # yearly growth when reported, else the latest quarter's
            "revenueGrowth": yoy_rev if yoy_rev is not None else _num(d["total_revenue_qoq_growth_fq"]),
            "epsGrowth": yoy_eps if yoy_eps is not None else _num(d["earnings_per_share_diluted_qoq_growth_fq"]),
            "roe": _num(d["return_on_equity"]),
            "netMargin": _num(d["net_margin"]),
            "pe": _num(d["price_earnings_ttm"]),
            "dividendYield": _num(d["dividends_yield_current"]),
            "nextEarnings": _num(d["earnings_release_next_date"]),
            "perf1m": _num(d["Perf.1M"]),
            "perf3m": _num(d["Perf.3M"]),
            "perf1y": _num(d["Perf.Y"]),
            "close": _num(d["close"]),
            "sma50": _num(d["SMA50"]),
            "sma200": _num(d["SMA200"]),
            "rsi": _num(d["RSI"]),
            "pb": _num(d["price_book_fq"]),
            "evEbitda": _num(d["enterprise_value_ebitda_ttm"]),
            "roic": _num(d["return_on_invested_capital"]),
            "opMargin": _num(d["operating_margin_ttm"]),
            "debtToEquity": _num(d["debt_to_equity_fq"]),
            "netDebt": _num(d["net_debt_fq"]),
            "ebitda": _num(d["ebitda_ttm"]),
            "currentRatio": _num(d["current_ratio_fq"]),
            "quickRatio": _num(d["quick_ratio_fq"]),
            "ocf": _num(d["cash_f_operating_activities_ttm"]),
            "netIncome": _num(d["net_income_ttm"]),
            "fcfMargin": _num(d["free_cash_flow_margin_ttm"]),
            "fcfYield": 100 / pfcf if (pfcf := _num(d["price_free_cash_flow_ttm"])) and pfcf > 0 else None,
            "payout": _num(d["dividend_payout_ratio_ttm"]),
            "value": _num(d["Value.Traded"]) or 0.0,  # EGP traded in the latest session
        })
    out = {"asOf": int(time.time()), "stocks": stocks}
    with _lock:
        _cache["fundamentals"] = out
    return out


def _median(xs: list) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _r1(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def _vote(x: float | None, lo: float, hi: float) -> int:
    """+1 above hi, -1 below lo, 0 in between or when unknown."""
    return 0 if x is None else 1 if x > hi else -1 if x < lo else 0


def _cap_weighted(stocks: list[dict], key: str) -> float | None:
    pairs = [(s["marketCap"], s[key]) for s in stocks if s.get(key) is not None and s["marketCap"]]
    w = sum(c for c, _ in pairs)
    return round(sum(c * v for c, v in pairs) / w, 1) if w else None


def _share_above(stocks: list[dict], key: str) -> float | None:
    """% of stocks closing above their `key` moving average; None when fewer than MIN_COMPANIES have one."""
    xs = [s for s in stocks if s.get("close") and s.get(key)]
    return round(sum(1 for s in xs if s["close"] > s[key]) / len(xs) * 100) if len(xs) >= MIN_COMPANIES else None


def technical(stocks: list[dict]) -> dict:
    """The sector's price trend: breadth above the 50/200-day averages and its cap-weighted 3-month move."""
    above50, above200 = _share_above(stocks, "sma50"), _share_above(stocks, "sma200")
    perf3m = _cap_weighted(stocks, "perf3m")
    rsi = _median([s.get("rsi") for s in stocks])
    if above50 is None or above200 is None:
        trend = "unknown"
    else:
        score = (_vote(above200, ABOVE_LO, ABOVE_HI - 0.001) + _vote(above50, ABOVE_LO, ABOVE_HI - 0.001)
                 + _vote(perf3m, -MOMENTUM_BAND, MOMENTUM_BAND))
        trend = "up" if score >= 2 else "down" if score <= -2 else "mixed"
    return {"trend": trend, "trendLabel": TREND_LABEL[trend], "above50": above50, "above200": above200,
            "perf1m": _cap_weighted(stocks, "perf1m"), "perf3m": perf3m, "rsi": _r1(rsi)}


def conclusion(status: str, tech: dict, events: list[dict]) -> dict:
    """One verdict from the financial status and the price trend, with notes on RSI and my upcoming events."""
    key, label, text = VERDICTS[(status, tech["trend"])]
    notes = []
    rsi = tech["rsi"]
    if rsi is not None and rsi >= RSI_HOT:
        notes.append(f"Overbought (median RSI {rsi:.0f}): avoid chasing, wait for a pullback.")
    elif rsi is not None and rsi <= RSI_COLD:
        notes.append(f"Oversold (median RSI {rsi:.0f}): selling may be overdone.")
    mine = [e for e in events if e["source"] == "mine"]
    pos, neg = sum(e["impact"] == "positive" for e in mine), sum(e["impact"] == "negative" for e in mine)
    if pos > neg:
        notes.append(f"Your upcoming events lean positive ({pos} positive, {neg} negative): a possible catalyst.")
    elif neg > pos:
        notes.append(f"Your upcoming events lean negative ({neg} negative, {pos} positive): a risk to watch.")
    tone = "bullish" if key in ("favour", "accumulate") else "bearish" if key in ("caution", "avoid") else "neutral"
    return {"verdict": key, "label": label, "tone": tone, "text": text, "notes": notes}


def _view_dict(view) -> dict:
    """A saved sector view (pydantic model or plain dict) as a plain dict."""
    if view is None:
        return {}
    return view.model_dump(mode="json") if hasattr(view, "model_dump") else dict(view)


def sector_outlook(stocks: list[dict], inflation_pct: float, views: dict, today: date) -> dict:
    """Growth / Normal / Shallow per sector from median real growth and how many companies are growing.
    My own view of a sector (status, note, events) is merged in, and its status wins over the suggestion."""
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        if s["sector"]:
            groups.setdefault(s["sector"], []).append(s)
    horizon = today + timedelta(days=EVENT_DAYS)
    rows = []
    for sector, members in groups.items():
        sl = slug(sector)
        rev = _median([s["revenueGrowth"] for s in members])
        eps_vals = [s["epsGrowth"] for s in members if s["epsGrowth"] is not None]
        eps = _median(eps_vals)
        with_data = sum(1 for s in members if s["revenueGrowth"] is not None or s["epsGrowth"] is not None)
        # share of companies whose earnings beat inflation, i.e. grew in real terms
        growing = round(sum(1 for x in eps_vals if x > inflation_pct) / len(eps_vals) * 100) if eps_vals else None
        real_rev = None if rev is None else rev - inflation_pct
        real_eps = None if eps is None else eps - inflation_pct

        reasons = []
        if with_data < MIN_COMPANIES:
            auto = "unknown"
            reasons.append(f"Only {with_data} of {len(members)} companies report growth figures: not enough to judge.")
        else:
            score = (_vote(real_rev, -REAL_GROWTH_BAND, REAL_GROWTH_BAND) + _vote(real_eps, -REAL_GROWTH_BAND, REAL_GROWTH_BAND)
                     + (0 if growing is None else 1 if growing >= GROWING_SHARE_HI else -1 if growing <= GROWING_SHARE_LO else 0))
            auto = "growth" if score >= 2 else "shallow" if score <= -2 else "normal"
            if rev is not None:
                reasons.append(f"Revenue {'up' if rev >= 0 else 'down'} {abs(rev):.0f}% at the median company "
                               f"(real {real_rev:+.0f}% after {inflation_pct:g}% inflation).")
            if eps is not None:
                reasons.append(f"Earnings per share {'up' if eps >= 0 else 'down'} {abs(eps):.0f}% at the median company "
                               f"(real {real_eps:+.0f}%).")
            if growing is not None:
                reasons.append(f"{growing}% of companies with figures grew their earnings faster than inflation.")

        view = _view_dict(views.get(sl))
        events, recent = [], []
        for ev in view.get("events", []):
            d = date.fromisoformat(str(ev["date"]))
            item = {"date": d.isoformat(), "title": ev["title"], "impact": ev.get("impact", "neutral"), "source": "mine"}
            (events if d >= today else recent).append(item)
        for s in members:
            if s["nextEarnings"]:
                d = datetime.fromtimestamp(s["nextEarnings"], tz=timezone.utc).date()
                if today <= d <= horizon:
                    events.append({"date": d.isoformat(), "title": f"{s['symbol'].replace('.CA', '')} results",
                                   "impact": "neutral", "source": "earnings", "symbol": s["symbol"]})
        events.sort(key=lambda e: e["date"])
        recent.sort(key=lambda e: e["date"], reverse=True)

        by_size = sorted(members, key=lambda s: -(s["marketCap"] or 0))
        status = view.get("status") or auto
        tech = technical(members)
        rows.append({
            "sector": sector, "slug": sl, "count": len(members), "withData": with_data,
            "status": status, "auto": auto, "override": bool(view.get("status")),
            "technical": tech, "conclusion": conclusion(status, tech, events),
            "note": view.get("note", ""), "updated": view.get("updated"), "myEvents": view.get("events", []),
            "marketCap": round(sum(s["marketCap"] or 0 for s in members)),
            "revenueGrowth": _r1(rev), "epsGrowth": _r1(eps), "realRevenueGrowth": _r1(real_rev), "realEpsGrowth": _r1(real_eps),
            "growingPct": growing,
            "roe": _r1(_median([s["roe"] for s in members])), "pe": _r1(_median([s["pe"] for s in members])),
            "dividendYield": _r1(_median([s["dividendYield"] for s in members])),
            "perf3m": _cap_weighted(members, "perf3m"), "perf1y": _cap_weighted(members, "perf1y"),
            "reasons": reasons, "events": events, "recent": recent[:5],
            "top": [{k: s[k] for k in ("symbol", "name", "marketCap", "revenueGrowth", "epsGrowth", "roe", "pe")}
                    for s in by_size[:5]],
        })
    rows.sort(key=lambda r: (VERDICT_ORDER[r["conclusion"]["verdict"]], STATUS_ORDER[r["status"]], -r["marketCap"]))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in STATUS_ORDER}
    verdicts = {k: sum(1 for r in rows if r["conclusion"]["verdict"] == k) for k in VERDICT_ORDER}
    return {"inflationPct": inflation_pct, "counts": counts, "verdicts": verdicts, "sectors": rows}
