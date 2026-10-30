"""Below fair value: EGX stocks priced under what their peers' multiples say they're worth.

Each stock gets up to three fair-price estimates from the median multiple of its peers (its industry when at least
MIN_PEERS companies there report the figure, else its sector):
  - P/E: price × peer P/E ÷ its P/E, i.e. its earnings valued at the price its peers trade at
  - P/B: the same with book value
  - EV/EBITDA: its enterprise value re-priced at the peer multiple, less its debt, per share (not for lenders)
Fair value is the median of the estimates (at least MIN_METHODS of them), each capped at MAX_RATIO× the price either
way so one odd figure (a one-off gain, a tiny book) can't decide it. A stock is cheap when its price is at least
`margin`% under its fair value.

Cheap isn't the same as a chance to buy: a low multiple often means the market expects worse. So each cheap stock
also gets the fundamental scorecard (rating.py) and its long-term trend (price vs the 200-day average):
  Buy zone           cheap, average or strong fundamentals, above its 200-day average
  Watch for a turn   cheap and sound, but below its 200-day average (or too new to tell)
  Check the figures  cheap, but too few figures to grade the business
  Cheap for a reason cheap, but weak fundamentals or losing money
This is relative value: when a whole sector is overpriced, its cheapest member can still be dear.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .rating import MIN_PEERS, fundamental_score, is_financial, sector_medians

MARGIN_PCT = 20.0   # default margin of safety: price at least this % under fair value
MAX_RATIO = 3.0     # one estimate can't put fair value more than this many times above or below the price
MIN_METHODS = 2     # fewer estimates than this: not valued
METHODS = [("pe", "P/E"), ("pb", "P/B"), ("evEbitda", "EV/EBITDA")]

# verdict -> label, tone, what it means
VERDICTS = {
    "buy": ("Buy zone", "bullish", "Cheap against its peers, a sound business, and the long-term trend is up."),
    "watch": ("Watch for a turn", "neutral", "Cheap and sound, but still below its 200-day average. Wait for the downtrend to end; "
                                             "don't catch a falling knife."),
    "unknown": ("Check the figures", "neutral", "Cheap, but too few figures to grade the business. Read its latest results first."),
    "trap": ("Cheap for a reason", "bearish", "Cheap, but the business is weak or losing money, so the low price may be deserved."),
}
VERDICT_RANK = {"buy": 0, "watch": 1, "unknown": 2, "trap": 3}


def peer_medians(funds: list[dict]) -> tuple[dict, dict]:
    """(by industry, by sector): the median of each multiple, from at least MIN_PEERS companies."""
    return sector_medians([f | {"sector": f.get("industry")} for f in funds]), sector_medians(funds)


def _peer(key: str, f: dict, by_industry: dict, by_sector: dict) -> tuple[float | None, str | None]:
    """The narrowest peer group with a median for this multiple, and its name."""
    for group, table in ((f.get("industry"), by_industry), (f.get("sector"), by_sector)):
        median = (table.get(group) or {}).get(key) if group else None
        if median:
            return median, group
    return None, None


def estimates(f: dict, by_industry: dict, by_sector: dict) -> list[dict]:
    """One fair price per multiple the stock and its peers both report."""
    price = f.get("close")
    if not price or price <= 0:
        return []
    out = []
    for key, label in METHODS:
        v = f.get(key)
        if v is None or v <= 0 or (key == "evEbitda" and is_financial(f)):
            continue
        median, peers = _peer(key, f, by_industry, by_sector)
        if not median:
            continue
        if key == "evEbitda":
            ebitda, mcap = f.get("ebitda"), f.get("marketCap")
            if not ebitda or ebitda <= 0 or not mcap or mcap <= 0:
                continue
            # EV = v × EBITDA = market cap + net debt; at the peer multiple the debt stays and the equity takes the change
            fair = price * (1 + (median - v) * ebitda / mcap)
        else:
            fair = price * median / v
        if fair <= 0:
            continue
        out.append({"key": key, "label": label, "value": round(v, 2), "peerMedian": median, "peers": peers,
                    "fair": round(min(max(fair, price / MAX_RATIO), price * MAX_RATIO), 2)})
    return out


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def verdict(f: dict, grade: str) -> str:
    if (f.get("pe") is not None and f["pe"] <= 0) or grade == "weak":
        return "trap"
    if grade == "unknown":
        return "unknown"
    above = f.get("close") and f.get("sma200") and f["close"] >= f["sma200"]
    return "buy" if above else "watch"


def value(f: dict, by_industry: dict, by_sector: dict, inflation: float) -> dict | None:
    """Fair value, discount and verdict for one stock; None when fewer than MIN_METHODS estimates."""
    est = estimates(f, by_industry, by_sector)
    if len(est) < MIN_METHODS:
        return None
    price, fair = f["close"], _median([e["fair"] for e in est])
    card = fundamental_score(f, by_sector.get(f.get("sector")), inflation)
    key = verdict(f, card["grade"])
    label, tone, text = VERDICTS[key]
    nxt = f.get("nextEarnings")
    return {
        "symbol": f["symbol"], "name": f.get("name"), "sector": f.get("sector"), "industry": f.get("industry"),
        "price": round(price, 2), "fair": round(fair, 2),
        "discountPct": round((1 - price / fair) * 100, 1), "upsidePct": round((fair / price - 1) * 100, 1),
        "methods": est,
        "grade": card["grade"], "gradeLabel": card["label"], "fundPct": card["pct"], "flags": [x["title"] for x in card["flags"]],
        "above200": None if not f.get("sma200") else price >= f["sma200"],
        "value": f.get("value"), "dividendYield": f.get("dividendYield"),
        "nextEarnings": datetime.fromtimestamp(nxt, tz=timezone.utc).date().isoformat() if nxt else None,
        "verdict": key, "label": label, "tone": tone, "text": text, "rank": VERDICT_RANK[key],
    }


def screen(funds: list[dict], inflation: float, margin: float = MARGIN_PCT) -> dict:
    """Every stock at least `margin`% under its fair value, best verdict first, then the deepest discount."""
    by_industry, by_sector = peer_medians(funds)
    valued = [v for f in funds if (v := value(f, by_industry, by_sector, inflation))]
    cheap = sorted((v for v in valued if v["discountPct"] >= margin), key=lambda v: (v["rank"], -v["discountPct"]))
    return {"marginPct": margin, "inflationPct": inflation, "total": len(funds), "valued": len(valued),
            "minPeers": MIN_PEERS, "counts": {k: sum(1 for v in cheap if v["verdict"] == k) for k in VERDICTS},
            "results": cheap}
