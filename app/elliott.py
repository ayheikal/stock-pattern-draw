"""An Elliott wave count of the latest swings: which wave is under way, where it usually ends, and where the count is wrong.

Swings come from a zig-zag: a turn counts once price moves a set percentage back from the last extreme. That
percentage is tried at several multiples of the series' usual daily range, so the count is made at whatever degree
fits it best. The last swing is still open (its extreme can still move), so it is the wave under way now.

Labels are fitted to the most recent swings only:
    0-1-2-3-4-5        an impulse: five waves with the trend
    5-A-B-C            the correction that follows it
    C-new              a correction finished, and a new move has started

The three hard rules reject a count: wave 2 never passes the start of wave 1, wave 3 is never the shortest of 1, 3
and 5, and wave 4 never enters wave 1's range. The usual proportions (wave 2 gives back 50-61.8% of 1, wave 3 runs
about 1.618 x wave 1, wave 4 gives back about 38.2% of 3, wave 5 about equals wave 1) score it; the best score wins.

Counts are subjective and often redrawn as new swings form: this is a guide to where a move stands, not a forecast.
"""
from __future__ import annotations

import numpy as np

LOOKBACK = 252                       # bars: about a year of daily bars
MULTIPLES = (3, 4, 5, 6, 8, 10, 13)  # zig-zag reversal sizes tried, in multiples of the average bar range
MIN_REVERSAL_PCT = 2.0               # never count a swing smaller than this
ATR_BARS = 100
MIN_SCORE = 0.6
IMPULSE = ("0", "1", "2", "3", "4", "5")
CORRECTION = ("A", "B", "C")
GOOD_READS = {"early", "done"}       # the gold checklist passes these


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.0f}"


def zigzag(high: np.ndarray, low: np.ndarray, pct: float) -> list[tuple[int, float, str]]:
    """Alternating swing points (index, price, "H"/"L"). The last one is the extreme of the swing still under way."""
    n, thr = len(high), pct / 100
    hi_i = lo_i = 0
    trend = 0  # 1 rising, -1 falling, 0 not yet known
    out: list[tuple[int, float, str]] = []
    for i in range(1, n):
        if trend >= 0 and high[i] >= high[hi_i]:
            hi_i = i
        if trend <= 0 and low[i] <= low[lo_i]:
            lo_i = i
        if trend == 0:
            if high[hi_i] >= low[lo_i] * (1 + thr) and lo_i < hi_i:
                out.append((lo_i, float(low[lo_i]), "L"))
                trend = 1
            elif low[lo_i] <= high[hi_i] * (1 - thr) and hi_i < lo_i:
                out.append((hi_i, float(high[hi_i]), "H"))
                trend = -1
        elif trend == 1 and low[i] <= high[hi_i] * (1 - thr):
            out.append((hi_i, float(high[hi_i]), "H"))
            trend, lo_i = -1, i
        elif trend == -1 and high[i] >= low[lo_i] * (1 + thr):
            out.append((lo_i, float(low[lo_i]), "L"))
            trend, hi_i = 1, i
    if trend == 1:
        out.append((hi_i, float(high[hi_i]), "H"))
    elif trend == -1:
        out.append((lo_i, float(low[lo_i]), "L"))
    return out


def _band(x: float, good: tuple[float, float], ok: tuple[float, float]) -> float:
    return 1.0 if good[0] <= x <= good[1] else 0.6 if ok[0] <= x <= ok[1] else 0.2


def _fit(pts: list[tuple[int, float, str]]) -> dict | None:
    """Labels for these swing points, the last one still open, or None when they break a rule."""
    L = len(pts)
    v = [p[1] for p in pts]
    d = 1 if pts[0][2] == "L" else -1          # an up count starts at a low
    up = lambda a, b: d * (b - a)              # a move measured in the count's direction
    done = L - 1                               # points before the open one are confirmed
    w = lambda k: abs(v[k] - v[k - 1])         # length of wave k (1-based over the impulse)
    parts: list[float] = []
    # hard rules, on confirmed points only
    if done >= 3 and not up(v[0], v[2]) > 0:                   # wave 2 passed the start of wave 1
        return None
    if done >= 4 and not up(v[1], v[3]) > 0:                   # wave 3 didn't pass wave 1's end
        return None
    if done >= 5 and not up(v[1], v[4]) > 0:                   # wave 4 entered wave 1's range
        return None
    if done >= 6 and (w(3) < w(1) and w(3) < w(5)):            # wave 3 the shortest
        return None
    if done >= 6 and not up(v[3], v[5]) > 0:                   # a truncated fifth: too rare to count on
        return None
    if done >= 8 and not up(v[7], v[5]) >= 0:                  # B went past the top of 5
        return None
    # proportions of the confirmed waves
    if done >= 3:
        parts.append(_band(w(2) / w(1), (0.382, 0.786), (0.236, 0.886)))
    if done >= 4:
        parts.append(_band(w(3) / w(1), (1.382, 2.618), (1.0, 4.236)))
    if done >= 5:
        parts.append(_band(w(4) / w(3), (0.236, 0.5), (0.146, 0.618)))
    if done >= 6:
        parts.append(_band(w(5) / w(1), (0.618, 1.618), (0.382, 2.618)))
    if done >= 8:
        parts.append(_band(w(7) / w(6), (0.382, 0.886), (0.236, 1.0)))
    if done >= 9:
        parts.append(_band(w(8) / w(6), (0.618, 1.618), (0.382, 2.618)))
    if not parts:
        return None
    names = (IMPULSE + CORRECTION + ("new",))[:L]
    return {"d": d, "v": v, "names": names, "score": float(np.mean(parts)), "parts": len(parts)}


MOVES_WITH_TREND = {"3", "5", "B", "new"}  # the wave under way moves the count's way; 4, A and C move against it


def _plan(f: dict, last: float) -> tuple[float, float]:
    """The usual end of the wave under way (the first of its usual targets price hasn't reached yet), and the price
    past which the count is wrong."""
    v, wave, d = f["v"], f["names"][-1], f["d"]
    if wave == "3":
        targets, inv = [v[2] + r * (v[1] - v[0]) for r in (1.618, 2.618)], v[2]
    elif wave == "4":
        targets, inv = [v[3] - r * (v[3] - v[2]) for r in (0.382, 0.5, 0.618)], v[1]
    elif wave == "5":
        targets, inv = [v[4] + (v[1] - v[0]), v[4] + 0.618 * (v[3] - v[0]), v[4] + (v[3] - v[0])], v[4]
    elif wave == "A":
        targets, inv = [v[4], v[2]], v[5]
    elif wave == "B":
        targets, inv = [v[6] + r * (v[5] - v[6]) for r in (0.5, 0.618, 0.786)], v[5]
    elif wave == "C":
        targets, inv = [v[7] + r * (v[6] - v[5]) for r in (1.0, 1.618)], v[5]
    else:  # a new move after A-B-C: back to the old extreme, then 1.618 x the correction's span past C; wrong past C
        targets, inv = [v[5], v[8] + 1.618 * (v[5] - v[8])], v[8]
    m = d if wave in MOVES_WITH_TREND else -d
    ahead = [t for t in sorted(targets, key=lambda t: m * t) if m * (t - last) > 0]
    return (ahead[0] if ahead else max(targets, key=lambda t: m * t)), inv


def _broken(d: int, wave: str, tip: float, last: float, inv: float) -> bool:
    """Has price, at the open swing's extreme or now, gone past the level the wave under way must hold? Waves of the
    impulse (and a new move) fail back past it, against the count; a correction fails past the top of wave 5."""
    if wave in CORRECTION:
        return d * (max(d * tip, d * last) * d - inv) > 0
    return d * (min(d * tip, d * last) * d - inv) < 0


def _read(d: int, wave: str, broken: bool) -> str:
    if broken:
        return "broken" if d == 1 else "turn"
    if wave in CORRECTION:
        return "corrective"
    if wave == "5":
        return "late"  # the last push: of a rise, a top is near; of a fall, a bottom is
    if d == 1:
        return {"3": "early", "4": "early", "new": "done"}[wave]
    return "bearish"


WAVE_WORDS = {
    "3": ("wave 3", "usually the longest and strongest"),
    "4": ("wave 4", "a pullback before a last push"),
    "5": ("wave 5", "the last push of the move"),
    "A": ("wave A", "the first leg of the correction after five waves"),
    "B": ("wave B", "a bounce inside the correction"),
    "C": ("wave C", "the last leg of the correction"),
    "new": ("a new move", "the A-B-C correction after five waves looks finished"),
}


def _text(read: str, d: int, wave: str, target: float, inv: float) -> str:
    trend = "rise" if d == 1 else "fall"
    name, note = WAVE_WORDS[wave]
    where = f"In {name} of a {trend}: {note}."
    t, i = _fmt(target), _fmt(inv)
    return {
        "early": f"{where} More of the {trend} usually lies ahead: this wave often reaches about {t}. The count is wrong past {i}.",
        "late": f"{where} It often ends near {t}, after which a correction usually takes back part of the {trend}. "
                f"Past {i} the fifth wave has likely ended.",
        "corrective": f"{where} It often reaches about {t}; past {i} the count is wrong.",
        "done": f"{where} A first aim is the old {'high' if d == 1 else 'low'} near {t}; past {i} the correction isn't over.",
        "bearish": f"{where} The count points lower: this wave often reaches about {t}. It is wrong past {i}.",
        "broken": f"The {trend} count is broken: price went past {i}, where wave {wave} should have held. Wait for a new count.",
        "turn": f"The {trend} count is broken: price went past {i}, so the fall may be over. Wait for a new count to confirm a rise.",
    }[read]


def count(high, low, lookback: int = LOOKBACK) -> dict | None:
    """The best Elliott wave count of the last `lookback` bars, or None. Takes highs and lows (pass closes for both
    on a close-only series). Label indices count from the start of the arrays."""
    high, low = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    n = len(high)
    if n < 30 or np.isnan(high).any() or np.isnan(low).any() or not (low > 0).all():
        return None
    start = max(0, n - lookback)
    h, lo = high[start:], low[start:]
    mid = (h + lo) / 2
    rng = np.maximum(h - lo, np.abs(np.diff(mid, prepend=mid[0])))  # a bar's range, or its move on a close-only series
    bar_pct = float(np.mean((rng / mid)[-ATR_BARS:]) * 100)
    last = (float(high[-1]) + float(low[-1])) / 2
    best, best_key = None, None
    for m in MULTIPLES:
        pts = zigzag(h, lo, max(MIN_REVERSAL_PCT, m * bar_pct))
        for L in range(4, 11):
            if len(pts) < L:
                break
            f = _fit(pts[-L:])
            if not f or f["score"] < MIN_SCORE:
                continue
            target, inv = _plan(f, last)
            broken = _broken(f["d"], f["names"][-1], f["v"][-1], last, inv)
            span = (len(h) - pts[-L][0]) / len(h)  # a count of the year's big swings beats one of the last few weeks
            key = (not broken, f["score"] + 0.04 * f["parts"] + 0.3 * span)
            if best_key is None or key > best_key:
                best_key = key
                best = (f, pts[-L:], target, inv, broken)
    if best is None:
        return None
    f, pts, target, inv, broken = best
    wave, d = f["names"][-1], f["d"]
    read = _read(d, wave, broken)
    rnd = lambda x: round(float(x), 4)
    return {
        "direction": "up" if d == 1 else "down",
        "wave": wave, "read": read, "score": round(f["score"], 2),
        "labels": [{"idx": start + i, "price": rnd(p), "kind": k, "label": name, "open": j == len(pts) - 1}
                   for j, ((i, p, k), name) in enumerate(zip(pts, f["names"]))],
        "target": rnd(target), "invalidation": rnd(inv),
        "text": _text(read, d, wave, target, inv),
    }
