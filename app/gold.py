"""Gold priced in Egyptian pounds: the world price (US dollars an ounce) times the dollar rate.

    1 g of 24k in EGP = gold in USD per ounce x USD/EGP / 31.1035
    21k and 18k are 87.5% and 75% of that; a gold pound is 8 g of 21k.

A move in the pound price is then two moves multiplied: gold itself, and the dollar against the pound. Splitting
them says whether you gained because gold rose or only because the pound fell.

This is the price the world market implies at the bank rate. A shop's price adds workmanship and tax, and the
local market can stand above or below it (the page works that gap out from a price you type in).
"""
from __future__ import annotations

import pandas as pd

from . import fibonacci
from .indicators import rsi

OUNCE_GRAMS = 31.1034768
KARATS = {"24": 1.0, "21": 0.875, "18": 0.75}
POUND_GRAMS, POUND_KARAT = 8.0, "21"
GOLD_SOURCES = ("TVC:GOLD", "OANDA:XAUUSD", "GC=F")  # spot first; the futures contract is the last resort
PERIODS = [("week", "1 week", pd.DateOffset(weeks=1)), ("month", "1 month", pd.DateOffset(months=1)),
           ("quarter", "3 months", pd.DateOffset(months=3)), ("year", "1 year", pd.DateOffset(years=1))]


def trade_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The calendar day each daily bar belongs to. Spot gold's bar opens the evening before in New York, so the
    plain date would be a day early; half a day past the bar's start in UTC is the right day for every source."""
    ix = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    return (ix + pd.Timedelta(hours=12)).tz_localize(None).normalize()


def by_day(closes: pd.Series) -> pd.Series:
    s = pd.Series(closes.to_numpy(dtype=float), index=trade_dates(closes.index)).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def combine(gold_usd: pd.Series, usd_egp: pd.Series) -> pd.DataFrame:
    """One row per day either traded: usd (an ounce), rate (USD/EGP) and gram24 (EGP), each carried over the days
    its own market was shut."""
    df = pd.concat({"usd": by_day(gold_usd), "rate": by_day(usd_egp)}, axis=1).sort_index().ffill().dropna()
    df["gram24"] = df["usd"] * df["rate"] / OUNCE_GRAMS
    return df


def prices(ounce_usd: float, rate: float) -> dict:
    gram24 = ounce_usd * rate / OUNCE_GRAMS
    grams = {k: round(gram24 * purity, 2) for k, purity in KARATS.items()}
    return {"ounceUsd": round(ounce_usd, 2), "rate": round(rate, 4), "ounceEgp": round(ounce_usd * rate, 2),
            "gram": grams, "pound": round(gram24 * KARATS[POUND_KARAT] * POUND_GRAMS, 2)}


def _pct(now: float, then: float) -> float:
    return round((now / then - 1) * 100, 2)


def _asof(s: pd.Series | None, day: pd.Timestamp) -> float | None:
    if s is None:
        return None
    v = s.asof(day)
    return None if pd.isna(v) else float(v)


def changes(df: pd.DataFrame, stocks: pd.Series | None = None) -> list[dict]:
    """Per look-back: gold in pounds, and the two moves behind it (gold in dollars, the dollar in pounds),
    with the stock index over the same days when given. egp is (1 + usd) x (1 + rate) - 1."""
    last, end = df.iloc[-1], df.index[-1]
    spans = [("day", "1 day", df.index[-2])]
    for k, label, back in PERIODS:  # the last day with a price on or before the calendar date
        at = df.index.asof(end - back)
        if not pd.isna(at):
            spans.append((k, label, at))
    before = df.index[df.index.year < end.year]
    if len(before):
        spans.append(("ytd", "This year", before[-1]))  # from last year's final price
    out = []
    for key, label, start in spans:
        then = df.loc[start]
        row = {"key": key, "label": label, "from": start.strftime("%Y-%m-%d"),
               "egp": _pct(last["gram24"], then["gram24"]), "usd": _pct(last["usd"], then["usd"]), "rate": _pct(last["rate"], then["rate"])}
        a, b = _asof(stocks, start), _asof(stocks, end)
        row["stocks"] = _pct(b, a) if a and b else None
        out.append(row)
    return out


def trend(s: pd.Series) -> dict:
    """Where the last value stands against its 50- and 200-day averages and its 1-year range."""
    last = float(s.iloc[-1])
    sma50 = float(s.rolling(50).mean().iloc[-1]) if len(s) >= 50 else None
    sma200 = float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
    year = s.tail(252)
    if sma50 and sma200:
        label = "Uptrend" if last > sma50 > sma200 else "Downtrend" if last < sma50 < sma200 else "Mixed"
    else:
        label = "Too little history"
    return {"trend": label, "above50": None if sma50 is None else last > sma50, "above200": None if sma200 is None else last > sma200,
            "vs200Pct": None if sma200 is None else _pct(last, sma200),
            "high": round(float(year.max()), 2), "low": round(float(year.min()), 2), "offHighPct": _pct(last, float(year.max()))}


def fib(s: pd.Series) -> dict | None:
    """Fibonacci retracements of the last 6 months' swing of a daily close series (see app/fibonacci.py)."""
    v = s.to_numpy(dtype=float)
    return fibonacci.retracement(v, v, float(v[-1])) if len(v) else None


def _fib_check(f: dict | None) -> dict:
    """A pullback into the 23.6-61.8% band of a rise is a good entry; a dip past 78.6% means the rise has failed."""
    label = "Gold in pounds is at a good entry"
    if not f:
        return _check("fib", label, "unknown", "No clear swing in the last 6 months to measure.")
    status = "pass" if f["read"] in fibonacci.GOOD_READS else "fail" if f["read"] == "broken" else "warn"
    return _check("fib", label, status, f["text"])


def explain(row: dict) -> str:
    """One look-back in words: how much of the pound move was gold and how much the pound."""
    up = lambda v: f"{'rose' if v >= 0 else 'fell'} {abs(v):.1f}%"
    gold, fx = abs(row["usd"]), abs(row["rate"])
    if gold + fx < 0.05:
        return f"Over the last {row['label'].lower()} gold in pounds barely moved."
    lead = ("mostly gold itself" if gold >= 2 * fx else "mostly the pound" if fx >= 2 * gold else "both gold and the pound")
    return (f"Over the last {row['label'].lower()}, gold in pounds {up(row['egp'])}: gold in dollars {up(row['usd'])} and the dollar {up(row['rate'])} against the pound, "
            f"so the move was {lead}.")


# --- Buy decision: gold in pounds is gold in dollars times the dollar in pounds, so both have to be read. ---
# Rule (static/gold.js applies the same one when a local premium is added; change both together):
#   2+ fails -> Hold off;  1 fail or 2+ warnings -> Wait;  otherwise -> Good time to buy.
GOLD_OVERBOUGHT_RSI = 70     # gold's 14-day RSI above this: stretched, prone to a pullback
GOLD_STRETCH_PCT = 8.0       # or this far above its 50-day average
RATE_STEADY_PCT = 1.0        # USD/EGP within this over 3 months: the pound is steady
RATE_FALL_FAIL_PCT = 3.0     # the dollar down more than this over 3 months: the pound is strengthening, a real headwind
RATE_JUMP_PCT = 5.0          # the dollar up this much in a month: a devaluation step gold in pounds has already priced in
VERDICTS = {"buy": "Good time to buy", "wait": "Wait", "avoid": "Hold off"}


def _check(key: str, label: str, status: str, detail: str) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


def verdict_of(checks: list[dict]) -> str:
    fails = sum(c["status"] == "fail" for c in checks)
    warns = sum(c["status"] == "warn" for c in checks)
    return "avoid" if fails >= 2 else "wait" if fails or warns >= 2 else "buy"


def _change_over(s: pd.Series, back: pd.DateOffset) -> float | None:
    at = s.index.asof(s.index[-1] - back)
    return None if pd.isna(at) else _pct(float(s.iloc[-1]), float(s.loc[at]))


def _trend_check(key: str, label: str, t: dict) -> dict:
    status = {"Uptrend": "pass", "Downtrend": "fail", "Mixed": "warn"}.get(t["trend"], "unknown")
    detail = (f"{t['trend']}: {'above' if t['above200'] else 'below'} its 200-day average ({t['vs200Pct']:+.1f}%), "
              f"{'above' if t['above50'] else 'below'} its 50-day." if t["above200"] is not None else "Too little history to read a trend.")
    return _check(key, label, status, detail)


def decide(df: pd.DataFrame) -> dict:
    """Is now a good time to buy gold with pounds? Reads gold in dollars, the dollar in pounds, and the two combined."""
    usd, rate, egp = df["usd"], df["rate"], df["gram24"] * KARATS["21"]
    checks = [_trend_check("gold", "Gold in dollars is rising", trend(usd))]

    r = float(rsi(usd.to_numpy(dtype=float))[-1]) if len(usd) > 15 else None
    sma50 = float(usd.rolling(50).mean().iloc[-1]) if len(usd) >= 50 else None
    stretch = _pct(float(usd.iloc[-1]), sma50) if sma50 else None
    if r is None or stretch is None:
        checks.append(_check("goldHeat", "Gold isn't overstretched", "unknown", "Too little history."))
    elif r > GOLD_OVERBOUGHT_RSI or stretch > GOLD_STRETCH_PCT:
        checks.append(_check("goldHeat", "Gold isn't overstretched", "warn",
                             f"RSI {r:.0f} and {stretch:+.1f}% from its 50-day average: after a run like this gold often pulls back. Consider buying in parts."))
    else:
        checks.append(_check("goldHeat", "Gold isn't overstretched", "pass", f"RSI {r:.0f}, {stretch:+.1f}% from its 50-day average."))

    q = _change_over(rate, pd.DateOffset(months=3))
    if q is None:
        checks.append(_check("rate", "The pound isn't strengthening", "unknown", "Too little dollar history."))
    elif q > RATE_STEADY_PCT:
        checks.append(_check("rate", "The pound isn't strengthening", "pass",
                             f"The dollar rose {q:.1f}% against the pound in 3 months: a weaker pound lifts gold in pounds on top of gold itself."))
    elif q >= -RATE_STEADY_PCT:
        checks.append(_check("rate", "The pound isn't strengthening", "pass",
                             f"The dollar is steady against the pound ({q:+.1f}% in 3 months), so gold in pounds follows gold in dollars."))
    else:
        checks.append(_check("rate", "The pound isn't strengthening", "fail" if q < -RATE_FALL_FAIL_PCT else "warn",
                             f"The dollar fell {abs(q):.1f}% against the pound in 3 months: a stronger pound takes that much off gold in pounds even if gold holds."))

    m = _change_over(rate, pd.DateOffset(months=1))
    if m is not None and m >= RATE_JUMP_PCT:
        checks.append(_check("rateJump", "The dollar hasn't just jumped", "warn",
                             f"The dollar jumped {m:.1f}% in a month: gold in pounds has already priced that in, and the pound often claws some of it back."))
    elif m is not None:
        checks.append(_check("rateJump", "The dollar hasn't just jumped", "pass", f"The dollar moved {m:+.1f}% against the pound this month."))

    checks.append(_trend_check("egp", "Gold in pounds is rising", trend(egp)))
    checks.append(_fib_check(fib(egp)))
    v = verdict_of(checks)
    return {"verdict": v, "label": VERDICTS[v], "reason": _reason(v, checks), "checks": checks}


PROBLEMS = {"gold": "gold in dollars isn't rising", "goldHeat": "gold is overstretched", "rate": "the pound is strengthening",
            "rateJump": "the dollar has just jumped", "egp": "gold in pounds isn't rising", "local": "the local price carries a premium",
            "fib": "gold in pounds isn't at a good entry on its swing"}


def _reason(v: str, checks: list[dict]) -> str:
    bad = [PROBLEMS.get(c["key"], c["label"].lower()) for c in checks if c["status"] in ("fail", "warn")]
    if not bad:
        return "Gold in dollars and the dollar in pounds both support gold in pounds right now."
    lead = {"buy": "Mostly supportive, but", "wait": "Not everything lines up:", "avoid": "Too much is against it:"}[v]
    return f"{lead} {', '.join(bad[:-1]) + ' and ' + bad[-1] if len(bad) > 1 else bad[0]}."


def _no_idx(f: dict | None) -> dict | None:
    return {k: v for k, v in f.items() if k not in ("highIdx", "lowIdx")} if f else None


def build(gold_usd: pd.Series, usd_egp: pd.Series, stocks: pd.Series | None = None, points: int = 1300) -> dict:
    """Everything the gold page shows, from daily closes of gold (USD an ounce), USD/EGP and, optionally, EGX 30."""
    df = combine(gold_usd, usd_egp)
    if len(df) < 2:
        raise ValueError("not enough overlapping gold and dollar history")
    last, prev = df.iloc[-1], df.iloc[-2]
    rows = changes(df, by_day(stocks) if stocks is not None else None)
    month = next((r for r in rows if r["key"] == "month"), rows[-1] if rows else None)
    return {
        "goldDate": by_day(gold_usd).index[-1].strftime("%Y-%m-%d"),
        "rateDate": by_day(usd_egp).index[-1].strftime("%Y-%m-%d"),
        "prices": prices(float(last["usd"]), float(last["rate"])),
        "previous": prices(float(prev["usd"]), float(prev["rate"])),
        "changes": rows,
        "explain": explain(month) if month else "",
        "trendEgp": trend(df["gram24"] * KARATS["21"]),
        "trendUsd": trend(df["usd"]),
        "trendRate": trend(df["rate"]),
        "decision": decide(df),
        # Per 24k gram in EGP (scale by karat), per ounce in USD, and USD/EGP; swing indices are dropped (the chart
        # draws the levels as horizontal lines)
        "fib": {k: _no_idx(fib(df[col])) for k, col in (("egp", "gram24"), ("usd", "usd"), ("rate", "rate"))},
        "karats": KARATS, "poundGrams": POUND_GRAMS, "ounceGrams": OUNCE_GRAMS,
        "series": [{"time": d.strftime("%Y-%m-%d"), "usd": round(float(r.usd), 2), "rate": round(float(r.rate), 4), "gram24": round(float(r.gram24), 2)}
                   for d, r in df.tail(points).iterrows()],
    }
