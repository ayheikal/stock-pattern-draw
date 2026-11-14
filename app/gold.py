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


def explain(row: dict) -> str:
    """One look-back in words: how much of the pound move was gold and how much the pound."""
    up = lambda v: f"{'rose' if v >= 0 else 'fell'} {abs(v):.1f}%"
    gold, fx = abs(row["usd"]), abs(row["rate"])
    if gold + fx < 0.05:
        return f"Over the last {row['label'].lower()} gold in pounds barely moved."
    lead = ("mostly gold itself" if gold >= 2 * fx else "mostly the pound" if fx >= 2 * gold else "both gold and the pound")
    return (f"Over the last {row['label'].lower()}, gold in pounds {up(row['egp'])}: gold in dollars {up(row['usd'])} and the dollar {up(row['rate'])} against the pound, "
            f"so the move was {lead}.")


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
        "karats": KARATS, "poundGrams": POUND_GRAMS, "ounceGrams": OUNCE_GRAMS,
        "series": [{"time": d.strftime("%Y-%m-%d"), "usd": round(float(r.usd), 2), "rate": round(float(r.rate), 4), "gram24": round(float(r.gram24), 2)}
                   for d, r in df.tail(points).iterrows()],
    }
