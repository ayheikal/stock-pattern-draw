"""The whole Egyptian Exchange in one request: every listed stock with sector, price, change and liquidity.

Source: TradingView's screener endpoint (unofficial, like the index feed in tradingview.py;
fine for personal use, see README). Cached for a few minutes. Data is delayed.
"""
from __future__ import annotations

import re
import threading
import time

import httpx
from cachetools import TTLCache

SCAN_URL = "https://scanner.tradingview.com/egypt/scan"
COLUMNS = ["name", "description", "sector", "close", "change", "volume", "Value.Traded", "market_cap_basic", "SMA50", "SMA200"]
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
