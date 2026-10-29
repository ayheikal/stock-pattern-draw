"""Fundamental + technical rating: two scorecards and one combined signal, from Strong buy to Sell.

Fundamentals score valuation, profitability, growth, debt, liquidity, cash flow and dividends, from TradingView's
EGX screener for EGX stocks and Yahoo Finance for the rest. Technicals score the price trend: moving averages,
momentum, RSI and the distance from the 52-week high. Chart patterns are left out on purpose: the Track record
shows they have no reliable edge.

Each item is strong / ok / weak (2 / 1 / 0 points), or "na" when it isn't reported or doesn't apply (banks and
other lenders skip debt, liquidity and cash-flow ratios). A scorecard is its points as a % of the points available
and needs a minimum of scored items to count. Valuation and margins are judged against the sector median when at
least MIN_PEERS companies report them; growth and returns are judged after inflation.

The combined signal crosses the fundamental grade with the trend, like the sector conclusion in egx.py.
Fundamentals can't be backtested (there's no history of past figures), so treat the signal as a summary, not a forecast.
"""
from __future__ import annotations

import math
import threading

import pandas as pd
from cachetools import TTLCache

MIN_PEERS = 3            # companies reporting a figure before its sector median is used
MIN_FUND_ITEMS = 5       # fewer scored fundamentals than this: grade unknown
MIN_TECH_ITEMS = 4       # fewer scored technical checks than this: trend unknown
FUND_STRONG, FUND_FAIR = 75, 50   # % of points: Strong at 75+, Average from 50, Weak below
TREND_UP, TREND_DOWN = 65, 35     # % of points: Uptrend at 65+, Downtrend at 35 or less
REAL_MARGIN = 5.0        # growth / returns this far above inflation are strong; above inflation is ok
PRICEY = 1.5             # a multiple up to this many times the sector median is ok; above is weak
FOREIGN_INFLATION_PCT = 3.0  # inflation used for stocks priced in other currencies than EGP

CATEGORIES = [
    ("valuation", "Valuation", "Is the price reasonable for what you get?"),
    ("profitability", "Profitability", "Does the business earn good returns?"),
    ("growth", "Growth", "Is it growing faster than inflation?"),
    ("health", "Debt", "Can it comfortably carry its debt?"),
    ("liquidity", "Liquidity", "Can it pay its short-term bills?"),
    ("cashflow", "Cash flow", "Do reported profits turn into cash?"),
    ("dividends", "Dividends", "Is the payout sustainable?"),
]
POINTS = {"strong": 2, "ok": 1, "weak": 0}
GRADE_LABEL = {"strong": "Strong", "fair": "Average", "weak": "Weak", "unknown": "Not enough data"}
TREND_LABEL = {"up": "Uptrend", "mixed": "Mixed", "down": "Downtrend", "unknown": "Not enough data"}
# Lenders and insurers: debt is their raw material, so debt, liquidity and cash-flow ratios don't mean the usual thing.
FINANCIAL_INDUSTRIES = {"Regional Banks", "Major Banks", "Investment Banks/Brokers", "Financial Conglomerates",
                        "Investment Managers", "Finance/Rental/Leasing", "Multi-Line Insurance",
                        "Property/Casualty Insurance", "Life/Health Insurance", "Specialty Insurance", "Insurance Brokers/Services"}

# (fundamental grade, trend) -> signal key, label, what it means
SIGNALS = {
    ("strong", "up"): ("strong-buy", "Strong buy", "A strong business in a rising trend: the fundamentals and the price agree."),
    ("strong", "mixed"): ("buy", "Buy", "A strong business, but the trend hasn't confirmed yet. Buy gradually, or wait for a breakout."),
    ("strong", "down"): ("hold", "Watch for a turn", "A strong business, but the price is falling. Wait for the downtrend to end; don't catch a falling knife."),
    ("fair", "up"): ("buy", "Buy (trend-led)", "Average fundamentals in a rising trend. Fine to ride with a stop; not a long-term case."),
    ("fair", "mixed"): ("hold", "Hold", "Average fundamentals and no clear trend: no edge either way."),
    ("fair", "down"): ("reduce", "Reduce", "Average fundamentals and a falling trend. Don't buy; if you hold it, tighten your stop or trim."),
    ("weak", "up"): ("hold", "Hold, tight stop", "The price is rising without fundamental support. Don't buy; if you hold it, keep a tight stop."),
    ("weak", "mixed"): ("reduce", "Reduce", "Weak fundamentals and no trend to lean on. Don't buy; if you hold it, consider trimming."),
    ("weak", "down"): ("sell", "Sell", "Weak fundamentals and a falling trend agree. Avoid it; if you hold it, review whether to sell."),
    ("unknown", "up"): ("hold", "Trend only", "Too few financial figures; the trend is up. A trading idea, not an investment case."),
    ("unknown", "mixed"): ("hold", "Not enough data", "Too few financial figures and no clear trend."),
    ("unknown", "down"): ("reduce", "Caution", "Too few financial figures and a falling trend."),
    ("strong", "unknown"): ("hold", "Fundamentals only", "A strong business; too little price history to judge the trend."),
    ("fair", "unknown"): ("hold", "Hold", "Average fundamentals; too little price history to judge the trend."),
    ("weak", "unknown"): ("reduce", "Reduce", "Weak fundamentals; too little price history to judge the trend."),
    ("unknown", "unknown"): ("unknown", "Not enough data", "Too few financial figures and too little price history."),
}
SIGNAL_RANK = {"strong-buy": 0, "buy": 1, "hold": 2, "reduce": 3, "sell": 4, "unknown": 5}
TONE = {"strong-buy": "bullish", "buy": "bullish", "hold": "neutral", "reduce": "bearish", "sell": "bearish", "unknown": "neutral"}


def _item(key: str, label: str, value, unit: str, status: str, rule: str, note: str = "") -> dict:
    return {"key": key, "label": label, "value": None if value is None else round(float(value), 2),
            "unit": unit, "status": status, "rule": rule, "note": note}


def _g(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 10 else f"{x:.1f}".rstrip("0").rstrip(".")


def _low(v: float, strong: float, ok: float) -> str:
    return "strong" if v <= strong else "ok" if v <= ok else "weak"


def _high(v: float, strong: float, ok: float) -> str:
    return "strong" if v >= strong else "ok" if v >= ok else "weak"


def is_financial(fund: dict | None) -> bool:
    f = fund or {}
    if f.get("source") == "yahoo":
        return f.get("sector") == "Financial Services"
    return f.get("industry") in FINANCIAL_INDUSTRIES


def inflation_for(currency: str | None, egp_inflation_pct: float) -> float:
    return egp_inflation_pct if currency in (None, "EGP") else FOREIGN_INFLATION_PCT


# --- fundamentals ---------------------------------------------------------------------------------

def _multiple(key: str, label: str, v: float | None, median: float | None, strong: float, ok: float, neg: str) -> dict:
    """A price multiple (lower is cheaper): against the sector median when there is one, else fixed bars."""
    if v is None:
        return _item(key, label, None, "×", "na", "", "Not reported.")
    if v <= 0:
        return _item(key, label, v, "×", "weak", "", neg)
    if median:
        return _item(key, label, v, "×", _low(v, median, median * PRICEY),
                     f"Strong ≤ sector median {_g(median)}× · OK ≤ {_g(median * PRICEY)}×")
    return _item(key, label, v, "×", _low(v, strong, ok), f"Strong ≤ {_g(strong)}× · OK ≤ {_g(ok)}×")


def _margin(key: str, label: str, v: float | None, median: float | None, strong: float, ok: float) -> dict:
    """A margin (higher is better): against the sector median when there is one, else fixed bars."""
    if v is None:
        return _item(key, label, None, "%", "na", "", "Not reported.")
    if v < 0:
        return _item(key, label, v, "%", "weak", "", "Losing money at this level.")
    if median and median > 0:
        return _item(key, label, v, "%", _high(v, median, median / 2), f"Strong ≥ sector median {_g(median)}% · OK ≥ {_g(median / 2)}%")
    return _item(key, label, v, "%", _high(v, strong, ok), f"Strong ≥ {_g(strong)}% · OK ≥ {_g(ok)}%")


def _real(key: str, label: str, v: float | None, inflation: float, what: str) -> dict:
    """Growth or a return judged after inflation: strong well above it, ok above it, weak below."""
    if v is None:
        return _item(key, label, None, "%", "na", "", "Not reported.")
    rule = f"Strong ≥ {_g(inflation + REAL_MARGIN)}% · OK ≥ {_g(inflation)}% inflation"
    note = f"{v - inflation:+.0f} pts after {_g(inflation)}% inflation."
    if v <= 0:
        return _item(key, label, v, "%", "weak", rule, f"{what} is shrinking." if key.endswith("Growth") else "Losing money for its owners.")
    return _item(key, label, v, "%", _high(v, inflation + REAL_MARGIN, inflation), rule, note)


def _skip(key: str, label: str, unit: str, value=None) -> dict:
    return _item(key, label, value, unit, "na", "", "Skipped for banks and other lenders: debt is their raw material.")


def fundamental_items(fund: dict | None, medians: dict | None, inflation: float) -> dict[str, list[dict]]:
    """Every scored fundamental, grouped by category."""
    f, m = fund or {}, medians or {}
    fin = is_financial(f)
    pe, eps = f.get("pe"), f.get("epsGrowth")
    out: dict[str, list[dict]] = {c: [] for c, _, _ in CATEGORIES}

    v = out["valuation"]
    v.append(_multiple("pe", "P/E", pe, m.get("pe"), 15, 25, "Negative P/E: the company is losing money."))
    v.append(_multiple("pb", "Price-to-book", f.get("pb"), m.get("pb"), 1.5, 3, "Negative book value."))
    v.append(_skip("evEbitda", "EV / EBITDA", "×", f.get("evEbitda")) if fin
             else _multiple("evEbitda", "EV / EBITDA", f.get("evEbitda"), m.get("evEbitda"), 10, 15, "Negative EBITDA."))
    real_eps = None if eps is None else eps - inflation
    if pe is None or pe <= 0 or real_eps is None:
        v.append(_item("peg", "PEG (real growth)", None, "×", "na", "", "Needs a positive P/E and EPS growth."))
    elif real_eps <= 0:
        v.append(_item("peg", "PEG (real growth)", None, "×", "na", "", "Earnings aren't growing faster than inflation."))
    else:
        v.append(_item("peg", "PEG (real growth)", pe / real_eps, "×", _low(pe / real_eps, 1, 2),
                       "P/E ÷ EPS growth after inflation · Strong ≤ 1 · OK ≤ 2"))
    fy = f.get("fcfYield")
    if fin:
        v.append(_skip("fcfYield", "Free cash flow yield", "%", fy))
    elif fy is None:
        v.append(_item("fcfYield", "Free cash flow yield", None, "%", "na", "", "Not reported."))
    else:
        v.append(_item("fcfYield", "Free cash flow yield", fy, "%", _high(fy, 6, 3), "Strong ≥ 6% · OK ≥ 3%",
                       "Spending more cash than it makes." if fy < 0 else ""))

    p = out["profitability"]
    p.append(_real("roe", "Return on equity", f.get("roe"), inflation, "Return"))
    p.append(_skip("roic", "Return on invested capital", "%", f.get("roic")) if fin
             else _real("roic", "Return on invested capital", f.get("roic"), inflation, "Return"))
    p.append(_skip("opMargin", "Operating margin", "%", f.get("opMargin")) if fin
             else _margin("opMargin", "Operating margin", f.get("opMargin"), m.get("opMargin"), 20, 10))
    p.append(_margin("netMargin", "Net margin", f.get("netMargin"), m.get("netMargin"), 15, 5))

    g = out["growth"]
    g.append(_real("revenueGrowth", "Revenue growth", f.get("revenueGrowth"), inflation, "Revenue"))
    g.append(_real("epsGrowth", "EPS growth", eps, inflation, "Earnings"))

    h = out["health"]
    de, nd, ebitda = f.get("debtToEquity"), f.get("netDebt"), f.get("ebitda")
    if fin:
        h += [_skip("debtToEquity", "Debt-to-equity", "×", de), _skip("netDebtEbitda", "Net debt / EBITDA", "×")]
    else:
        if de is None:
            h.append(_item("debtToEquity", "Debt-to-equity", None, "×", "na", "", "Not reported."))
        elif de < 0:
            h.append(_item("debtToEquity", "Debt-to-equity", de, "×", "weak", "", "Negative equity: liabilities exceed assets."))
        else:
            h.append(_item("debtToEquity", "Debt-to-equity", de, "×", _low(de, 0.5, 1), "Strong ≤ 0.5× · OK ≤ 1×"))
        rule = "Strong ≤ 1× · OK ≤ 3×"
        if nd is None:
            h.append(_item("netDebtEbitda", "Net debt / EBITDA", None, "×", "na", "", "Not reported."))
        elif nd <= 0:
            h.append(_item("netDebtEbitda", "Net debt / EBITDA", nd / ebitda if ebitda and ebitda > 0 else None, "×",
                           "strong", rule, "More cash than debt."))
        elif not ebitda or ebitda <= 0:
            h.append(_item("netDebtEbitda", "Net debt / EBITDA", None, "×", "weak" if ebitda is not None else "na", rule,
                           "Has debt but no operating profit to pay it." if ebitda is not None else "No EBITDA reported."))
        else:
            h.append(_item("netDebtEbitda", "Net debt / EBITDA", nd / ebitda, "×", _low(nd / ebitda, 1, 3), rule))

    lq = out["liquidity"]
    for key, label, strong, ok in (("currentRatio", "Current ratio", 1.5, 1.0), ("quickRatio", "Quick ratio", 1.0, 0.7)):
        x = f.get(key)
        if fin:
            lq.append(_skip(key, label, "×", x))
        elif x is None:
            lq.append(_item(key, label, None, "×", "na", "", "Not reported."))
        else:
            lq.append(_item(key, label, x, "×", _high(x, strong, ok), f"Strong ≥ {_g(strong)}× · OK ≥ {_g(ok)}×"))

    cf = out["cashflow"]
    ocf, ni = f.get("ocf"), f.get("netIncome")
    if fin:
        cf += [_skip("cashConversion", "Cash conversion", "×"), _skip("fcfMargin", "Free cash flow margin", "%", f.get("fcfMargin"))]
    else:
        if ocf is None or ni is None:
            cf.append(_item("cashConversion", "Cash conversion", None, "×", "na", "", "Not reported."))
        elif ni <= 0:
            cf.append(_item("cashConversion", "Cash conversion", None, "×", "na", "", "No profit to compare cash flow with."))
        else:
            cf.append(_item("cashConversion", "Cash conversion", ocf / ni, "×", _high(ocf / ni, 1, 0.8),
                            "Operating cash flow ÷ net income · Strong ≥ 1× · OK ≥ 0.8×"))
        fm = f.get("fcfMargin")
        if fm is None:
            cf.append(_item("fcfMargin", "Free cash flow margin", None, "%", "na", "", "Not reported."))
        else:
            cf.append(_item("fcfMargin", "Free cash flow margin", fm, "%", _high(fm, 10, 5), "Strong ≥ 10% · OK ≥ 5%",
                            "Spending more cash than it makes." if fm < 0 else ""))

    dv = out["dividends"]
    pay = f.get("payout")
    if not pay or pay <= 0:
        dv.append(_item("payout", "Payout ratio", None, "%", "na", "", "No dividend, or not reported."))
    else:
        dv.append(_item("payout", "Payout ratio", pay, "%", _low(pay, 60, 80), "Strong ≤ 60% · OK ≤ 80%",
                        "Paying out more than it earns." if pay > 100 else ""))
    dy = f.get("dividendYield")
    dv.append(_item("dividendYield", "Dividend yield", dy, "%", "info", "For information; not scored.", "" if dy else "No dividend reported."))
    return out


def fundamental_flags(fund: dict | None, inflation: float) -> list[dict]:
    """Warning signs worth a look whatever the score."""
    f = fund or {}
    fin, flags = is_financial(f), []
    add = lambda title, text: flags.append({"title": title, "text": text})
    if f.get("pe") is not None and f["pe"] <= 0:
        add("Loss-making", "A negative P/E means the company lost money over the last year.")
    if f.get("revenueGrowth") is not None and f["revenueGrowth"] < 0:
        add("Shrinking revenue", "Sales are lower than a year ago.")
    if not fin:
        ocf, ni = f.get("ocf"), f.get("netIncome")
        if ocf is not None and ni and ni > 0 and ocf / ni < 0.8:
            add("Weak cash conversion", "Operating cash flow is well below reported profit. Check receivables, inventory and one-off gains.")
        de, roe = f.get("debtToEquity"), f.get("roe")
        if de is not None and de < 0:
            add("Negative equity", "Liabilities exceed assets on the balance sheet.")
        if de is not None and roe is not None and roe >= 20 and de > 2:
            add("Debt-driven returns", "A high ROE alongside heavy debt: returns may come from leverage rather than the business.")
        nd, ebitda = f.get("netDebt"), f.get("ebitda")
        if nd and nd > 0 and ebitda and ebitda > 0 and nd / ebitda > 4:
            add("Heavy debt", f"Net debt is {nd / ebitda:.1f}× a year's operating profit.")
        if f.get("currentRatio") is not None and f["currentRatio"] < 1:
            add("Tight liquidity", "Short-term liabilities exceed short-term assets.")
        if f.get("roic") is not None and f["roic"] < inflation:
            add("Returns below inflation", f"Return on invested capital ({f['roic']:.0f}%) is under {_g(inflation)}% inflation: growth destroys value.")
    if f.get("payout") and f["payout"] > 100:
        add("Dividend exceeds earnings", "The payout ratio is over 100%, so the dividend is funded from reserves or debt.")
    return flags


def _scorecard(groups: dict[str, list[dict]], min_items: int) -> dict:
    total = mx = scored = 0
    cats = []
    for key, label, question in CATEGORIES:
        items = groups.get(key, [])
        pts = [POINTS[i["status"]] for i in items if i["status"] in POINTS]
        cats.append({"key": key, "label": label, "question": question, "points": sum(pts), "max": 2 * len(pts),
                     "pct": round(sum(pts) / (2 * len(pts)) * 100) if pts else None, "items": items})
        total, mx, scored = total + sum(pts), mx + 2 * len(pts), scored + len(pts)
    pct = round(total / mx * 100) if mx and scored >= min_items else None
    return {"points": total, "max": mx, "scored": scored, "pct": pct, "categories": cats}


def fundamental_score(fund: dict | None, medians: dict | None, inflation: float) -> dict:
    card = _scorecard(fundamental_items(fund, medians, inflation), MIN_FUND_ITEMS)
    pct = card["pct"]
    grade = "unknown" if pct is None else "strong" if pct >= FUND_STRONG else "fair" if pct >= FUND_FAIR else "weak"
    return card | {"grade": grade, "label": GRADE_LABEL[grade], "flags": fundamental_flags(fund, inflation),
                   "financial": is_financial(fund), "inflationPct": inflation}


# --- technicals -----------------------------------------------------------------------------------

# Bars per look-back on each interval: the long / short averages, the averages' slope, ~6 months and ~1 year.
TECH_BARS = {"1d": {"long": 200, "short": 50, "slope": 20, "m6": 126, "year": 252, "unit": "day"},
             "1wk": {"long": 40, "short": 10, "slope": 4, "m6": 26, "year": 52, "unit": "week"}}


def _rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else math.inf
    return float(100 - 100 / (1 + rs))


def technical_items(df: pd.DataFrame, interval: str) -> list[dict]:
    b = TECH_BARS.get(interval)
    if b is None or df is None or len(df) < 30:
        return []
    close, n, u = df["Close"], len(df), b["unit"]
    last = float(close.iloc[-1])
    long_ = close.rolling(b["long"]).mean()
    short = close.rolling(b["short"]).mean()
    L, S = f"{b['long']}-{u}", f"{b['short']}-{u}"
    out = []
    if n >= b["long"]:
        lv = float(long_.iloc[-1])
        out.append(_item("aboveLong", f"Above its {L} average", (last / lv - 1) * 100, "%", "strong" if last >= lv else "weak",
                         "Strong above · Weak below", "The long-term trend is up." if last >= lv else "Below it: a long-term downtrend."))
    else:
        out.append(_item("aboveLong", f"Above its {L} average", None, "%", "na", "", f"Needs {b['long']} bars of history."))
    sv = float(short.iloc[-1])
    out.append(_item("aboveShort", f"Above its {S} average", (last / sv - 1) * 100, "%", "strong" if last >= sv else "weak",
                     "Strong above · Weak below"))
    if n >= b["long"]:
        cross = float(short.iloc[-1]) >= float(long_.iloc[-1])
        out.append(_item("cross", f"{S} average above the {L}", None, "", "strong" if cross else "weak", "Strong above · Weak below",
                         "Golden cross: medium-term trend above the long-term one." if cross
                         else "Death cross: medium-term trend below the long-term one."))
    else:
        out.append(_item("cross", f"{S} average above the {L}", None, "", "na", "", f"Needs {b['long']} bars of history."))
    if n >= b["long"] + b["slope"]:
        slope = (float(long_.iloc[-1]) / float(long_.iloc[-1 - b["slope"]]) - 1) * 100
        out.append(_item("slope", f"{L} average rising", slope, "%", "strong" if slope > 1 else "weak" if slope < -1 else "ok",
                         f"Change over {b['slope']} bars · Strong > +1% · Weak < −1%"))
    else:
        out.append(_item("slope", f"{L} average rising", None, "%", "na", "", "Not enough history."))
    if n > b["m6"]:
        mom = (last / float(close.iloc[-1 - b["m6"]]) - 1) * 100
        out.append(_item("momentum", "6-month momentum", mom, "%", _high(mom, 10, 0), "Strong ≥ +10% · OK ≥ 0%"))
    else:
        out.append(_item("momentum", "6-month momentum", None, "%", "na", "", "Less than 6 months of history."))
    rsi = _rsi(close)
    if rsi > 70:
        st, note = "ok", "Overbought: the rise is stretched, so wait for a pullback before buying."
    elif rsi >= 50:
        st, note = "strong", "Buyers in control."
    elif rsi >= 30:
        st, note = "ok", "Momentum is soft."
    else:
        st, note = "weak", "Oversold: sellers in control. A bounce is possible, but the trend is down."
    out.append(_item("rsi", "RSI (14)", rsi, "", st, "Strong 50–70 · OK 30–50 or above 70 · Weak below 30", note))
    if n >= b["year"] * 0.8:
        hi = float(df["High"].tail(b["year"]).max())
        dist = (last / hi - 1) * 100
        out.append(_item("nearHigh", "Distance from 52-week high", dist, "%", _high(dist, -10, -25), "Strong within 10% · OK within 25%"))
    else:
        out.append(_item("nearHigh", "Distance from 52-week high", None, "%", "na", "", "Less than a year of history."))
    return out


def technical_score(df: pd.DataFrame, interval: str) -> dict:
    items = technical_items(df, interval)
    pts = [POINTS[i["status"]] for i in items if i["status"] in POINTS]
    pct = round(sum(pts) / (2 * len(pts)) * 100) if len(pts) >= MIN_TECH_ITEMS else None
    trend = "unknown" if pct is None else "up" if pct >= TREND_UP else "down" if pct <= TREND_DOWN else "mixed"
    rsi = next((i["value"] for i in items if i["key"] == "rsi"), None)
    return {"items": items, "points": sum(pts), "max": 2 * len(pts), "scored": len(pts), "pct": pct,
            "trend": trend, "label": TREND_LABEL[trend], "rsi": rsi}


# --- combined -------------------------------------------------------------------------------------

def combine(fundamental: dict, technical: dict | None) -> dict:
    """The signal from the fundamental grade and the trend, with a blended 0-100 score and notes."""
    trend = (technical or {}).get("trend", "unknown")
    key, label, text = SIGNALS[(fundamental["grade"], trend)]
    notes = []
    rsi = (technical or {}).get("rsi")
    if rsi is not None and rsi > 70 and key in ("strong-buy", "buy"):
        notes.append(f"Overbought (RSI {rsi:.0f}): wait for a pullback before buying.")
    if fundamental["flags"]:
        notes.append(f"{len(fundamental['flags'])} red flag{'s' if len(fundamental['flags']) != 1 else ''} in the financials: see below.")
    fp, tp = fundamental["pct"], (technical or {}).get("pct")
    score = round((fp + tp) / 2) if fp is not None and tp is not None else None
    return {"key": key, "label": label, "tone": TONE[key], "rank": SIGNAL_RANK[key], "text": text, "notes": notes, "score": score}


def rate(fund: dict | None, medians: dict | None, inflation: float, technical: dict | None) -> dict:
    """Both scorecards and the combined signal for one stock."""
    fundamental = fundamental_score(fund, medians, inflation)
    source = None if not fund else "Yahoo Finance" if fund.get("source") == "yahoo" else "TradingView"
    return {"fundamental": fundamental, "technical": technical, "signal": combine(fundamental, technical), "source": source,
            "note": "Fundamentals can't be backtested (there's no history of past figures), so this is a summary, not a forecast. "
                    "Chart patterns aren't part of it: the Track record shows they have no reliable edge."}


def sector_medians(funds: list[dict]) -> dict[str, dict]:
    """{sector: {"pe", "pb", "evEbitda", "opMargin", "netMargin"}}: medians from at least MIN_PEERS companies each."""
    from .egx import _median
    groups: dict[str, list[dict]] = {}
    for s in funds:
        if s.get("sector"):
            groups.setdefault(s["sector"], []).append(s)
    out = {}
    for sector, members in groups.items():
        row = {}
        for k in ("pe", "pb", "evEbitda", "opMargin", "netMargin"):
            xs = [s[k] for s in members if s.get(k) is not None and s[k] > 0]  # loss-makers would drag a multiple's median down
            row[k] = round(_median(xs), 2) if len(xs) >= MIN_PEERS else None
        out[sector] = row
    return out


# --- Yahoo Finance fundamentals (stocks outside the EGX) ------------------------------------------

_yahoo_cache: TTLCache = TTLCache(maxsize=512, ttl=6 * 3600)
_yahoo_lock = threading.Lock()


def _yahoo_info(symbol: str) -> dict:
    import yfinance as yf
    return yf.Ticker(symbol).info or {}


def yahoo_fund(info: dict) -> dict | None:
    """Yahoo's quote summary in the same shape as egx.fetch_fundamentals' rows; None for funds, crypto, futures..."""
    if not info or info.get("quoteType") != "EQUITY":
        return None
    num = lambda k: float(info[k]) if isinstance(info.get(k), (int, float)) and not isinstance(info.get(k), bool) else None
    pct = lambda k: None if num(k) is None else num(k) * 100
    rev, fcf, mcap = num("totalRevenue"), num("freeCashflow"), num("marketCap")
    debt, cash, de = num("totalDebt"), num("totalCash"), num("debtToEquity")
    price, rate = num("currentPrice") or num("regularMarketPrice"), num("dividendRate")
    return {
        "symbol": info.get("symbol"), "name": info.get("longName") or info.get("shortName"), "source": "yahoo",
        "sector": info.get("sector"), "industry": info.get("industry"), "marketCap": mcap,
        "pe": num("trailingPE"), "pb": num("priceToBook"), "evEbitda": num("enterpriseToEbitda"),
        "roe": pct("returnOnEquity"), "roic": None, "opMargin": pct("operatingMargins"), "netMargin": pct("profitMargins"),
        "revenueGrowth": pct("revenueGrowth"), "epsGrowth": pct("earningsGrowth"),  # latest quarter vs a year earlier
        "debtToEquity": de / 100 if de is not None else None,  # Yahoo reports it as a percentage
        "currentRatio": num("currentRatio"), "quickRatio": num("quickRatio"),
        "netDebt": debt - cash if debt is not None and cash is not None else None, "ebitda": num("ebitda"),
        "ocf": num("operatingCashflow"), "netIncome": num("netIncomeToCommon"),
        "fcfMargin": fcf / rev * 100 if fcf is not None and rev else None,
        "fcfYield": fcf / mcap * 100 if fcf is not None and mcap else None,
        "payout": pct("payoutRatio"),
        "dividendYield": rate / price * 100 if rate and price else None,
        "nextEarnings": num("earningsTimestampStart") or num("earningsTimestamp"),
    }


def fetch_yahoo_fundamentals(symbol: str) -> dict | None:
    """Yahoo fundamentals for a non-EGX stock, cached for hours (also when Yahoo has none, so a scan isn't slowed twice)."""
    code = symbol.strip().upper()
    with _yahoo_lock:
        if code in _yahoo_cache:
            return _yahoo_cache[code]
    try:
        out = yahoo_fund(_yahoo_info(code))
    except Exception:  # offline or rate-limited: no fundamentals, the checks say "unknown"
        return None
    with _yahoo_lock:
        _yahoo_cache[code] = out
    return out
