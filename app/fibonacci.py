"""Fibonacci retracements of the last big swing: where a pullback is a good entry, and where the idea is wrong.

The swing ends at the later of the highest high and the lowest low of the last LOOKBACK bars, and starts at the
opposite extreme before it, looked for up to twice as far back (so a move already under way when the window opens
is measured from where it really began). If the low came first the swing is a rise ("up"), and a pullback that gives back 38.2-61.8% of it is the classic place to buy, with the stop under
the 78.6% level: past that the rise has usually failed. If the high came first the swing is a fall ("down"),
and the same ratios measure the bounce: a long-only buyer waits for it to take back most of the fall.
"""
from __future__ import annotations

import numpy as np

RETRACE = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTEND = (1.272, 1.618)
LOOKBACK = 126        # bars: about 6 months of daily bars
MIN_SWING_PCT = 5.0   # a smaller high-to-low swing is noise, not a swing to measure
MIN_BARS = 10
ENTRY_RATIO = 0.5     # the buy-limit when price hasn't pulled back yet
STOP_RATIO = 0.786    # given back more than this, the rise has likely failed
GOOD_READS = {"shallow", "zone"}


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.0f}"


def _read(up: bool, r: float) -> str:
    if up:
        return "top" if r < 0.236 else "shallow" if r < 0.382 else "zone" if r <= 0.618 else "deep" if r <= 0.786 else "broken"
    return "falling" if r < 0.382 else "bounce" if r < 0.786 else "reclaimed"


def _text(read: str, r: float, lo: float, hi: float, at: dict, zone: tuple[float, float]) -> str:
    pct, zl, zh = f"{r * 100:.0f}%", _fmt(zone[0]), _fmt(zone[1])
    rise, fall = f"the rise from {_fmt(lo)} to {_fmt(hi)}", f"the fall from {_fmt(hi)} to {_fmt(lo)}"
    return {
        "top": f"Near the top of {rise} (gave back {pct}). A dip to the 38.2–61.8% zone ({zl}–{zh}) is a better entry than chasing.",
        "shallow": f"A shallow pullback: gave back {pct} of {rise}. Firmer support is in the 38.2–61.8% zone below ({zl}–{zh}).",
        "zone": f"Pulled back {pct} of {rise}, inside the 38.2–61.8% zone ({zl}–{zh}) where uptrends often resume.",
        "deep": f"A deep pullback: gave back {pct} of {rise}. Still above the 78.6% level ({_fmt(at[0.786])}); below it the rise is likely over.",
        "broken": f"Gave back {pct} of {rise}, past the 78.6% level ({_fmt(at[0.786])}): the rise has likely failed.",
        "falling": f"Still near the low of {fall} (took back {pct}). Wait for a close above the 61.8% level ({_fmt(at[0.618])}) before buying.",
        "bounce": (f"Bounced {pct} of {fall}, into the 38.2–61.8% zone ({zl}–{zh}) where falls often resume. "
                   f"Wait for a close above the 61.8% level ({_fmt(at[0.618])})." if r <= 0.618 else
                   f"Took back {pct} of {fall}. A close above the 78.6% level ({_fmt(at[0.786])}) would show buyers have taken over."),
        "reclaimed": f"Took back {pct} of {fall}: buyers have recovered most of it.",
    }[read]


def retracement(high, low, last: float, buffer: float = 0.0, lookback: int = LOOKBACK) -> dict | None:
    """The last big swing in the window and where `last` stands in it, with an entry, stop and target for an up
    swing. Takes highs and lows (pass closes for both on a close-only series). None when there is no clear swing."""
    high, low = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    n = len(high)
    if n < MIN_BARS or not np.isfinite(last):
        return None
    start, reach = max(0, n - lookback), max(0, n - 2 * lookback)
    if np.isnan(high[start:]).all() or np.isnan(low[start:]).all():
        return None
    hi_i, lo_i = start + int(np.nanargmax(high[start:])), start + int(np.nanargmin(low[start:]))
    # the later extreme ends the swing; the earlier one may lie before the window
    if lo_i <= hi_i:
        lo_i = reach + int(np.nanargmin(low[reach:hi_i + 1]))
    else:
        hi_i = reach + int(np.nanargmax(high[reach:lo_i + 1]))
    hi, lo = float(high[hi_i]), float(low[lo_i])
    if not lo > 0 or (hi / lo - 1) * 100 < MIN_SWING_PCT:
        return None
    span, up = hi - lo, lo_i <= hi_i
    last = min(max(float(last), lo), hi)
    r = round((hi - last) / span if up else (last - lo) / span, 6)  # so a price exactly on a level reads as on it
    # Up swing: the levels step down from the high; down swing: the bounce levels step up from the low.
    at = {k: (hi - k * span if up else lo + k * span) for k in RETRACE}
    ext = {k: (lo + k * span if up else max(hi - k * span, 0.0)) for k in EXTEND}
    zone = tuple(sorted((at[0.382], at[0.618])))
    read = _read(up, r)
    entry = stop = target = None
    if up and read != "broken":
        entry = last if r >= 0.382 else at[ENTRY_RATIO]
        stop = max(at[STOP_RATIO] - buffer, 0.0)
        target = hi if last < hi * 0.999 else ext[1.272]
    rnd = lambda v: None if v is None else round(float(v), 4)
    return {
        "direction": "up" if up else "down",
        "high": rnd(hi), "low": rnd(lo), "highIdx": hi_i, "lowIdx": lo_i,
        "retracePct": round(r * 100, 1), "read": read, "text": _text(read, r, lo, hi, at, zone),
        "levels": [{"ratio": k, "price": rnd(v)} for k, v in at.items()],
        "extensions": [{"ratio": k, "price": rnd(v)} for k, v in ext.items()],
        "zone": {"low": rnd(zone[0]), "high": rnd(zone[1])},
        "entry": rnd(entry), "stop": rnd(stop), "target": rnd(target),
    }


def level(fib: dict | None, ratio: float) -> float | None:
    """One retracement level's price, or None."""
    if not fib:
        return None
    return next((x["price"] for x in fib["levels"] if x["ratio"] == ratio), None)
