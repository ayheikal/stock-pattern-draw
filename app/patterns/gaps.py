"""Price gaps: breakaway, runaway (measuring) and exhaustion gaps, and island reversals."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import project, target_segment
from .models import Pattern, Point

MIN_GAP = 0.4  # a gap must span at least this many ATRs; smaller ones are noise
CONTEXT = 20  # bars before a gap that set its context: a range it breaks out of, or a trend it extends
FILL_BARS = 5  # a gap still open this many bars later has held
MAX_ISLAND = 15  # most bars an island may hold between its two gaps

LABELS = {
    "Breakaway Gap": "BG",
    "Runaway Gap": "RG",
    "Exhaustion Gap": "EG",
    "Island Top": "IT",
    "Island Bottom": "IB",
}


def _sides(df: pd.DataFrame, up: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """High, low and close with down moves mirrored into up moves, so one routine handles both sides."""
    high, low, close = (df[c].to_numpy(dtype=float) for c in ("High", "Low", "Close"))
    return (high, low, close) if up else (-low, -high, -close)


def _box(i0: int, i1: int, a: float, b: float) -> list[list[Point]]:
    return [[(i0, a), (i1, a)], [(i0, b), (i1, b)]]


def _islands(df: pd.DataFrame, atr: np.ndarray, up: bool) -> tuple[list[Pattern], set[int]]:
    """Island tops (or, mirrored, bottoms): a gap with the trend, then a gap back that leaves the bars between stranded."""
    hi, lo, c = _sides(df, up)
    sgn = 1 if up else -1
    n = len(c)
    out, used = [], set()
    for i in range(CONTEXT, n - 1):
        a = atr[i - 1]
        if lo[i] - hi[i - 1] < MIN_GAP * a or (c[i - 1] - c[i - CONTEXT]) / a < 1.5:
            continue
        for j in range(i + 1, min(n, i + 1 + MAX_ISLAND)):
            if lo[j - 1] <= hi[i - 1]:
                break  # the first gap filled: nothing is stranded
            base = lo[i:j].min()
            if base - hi[j] >= MIN_GAP * atr[j - 1]:
                peak = i + int(np.argmax(hi[i:j]))
                edge = max(hi[i - 1], hi[j])
                name = "Island Top" if up else "Island Bottom"
                out.append(Pattern(
                    name=name,
                    category="gap",
                    direction="bearish" if up else "bullish",
                    start=i - 1,
                    end=j,
                    confidence=round(min(0.7 + 0.05 * min(j - i, 4), 0.95), 2),
                    # the island's extreme comes last: a move back beyond it undoes the reversal
                    points=[(i - 1, sgn * hi[i - 1]), (j, sgn * hi[j]), (peak, sgn * hi[peak])],
                    lines=_box(i - 1, j, sgn * base, sgn * edge),
                    confirmed=True,
                    label=LABELS[name],
                ))
                used |= {i, j}
                break
    return out, used


def _gaps(df: pd.DataFrame, atr: np.ndarray, up: bool, skip: set[int]) -> list[Pattern]:
    """Gaps up (or, mirrored, down) classed by what came before and whether they held.

    Out of a quiet range to a new high: breakaway. After a strong run: runaway if it holds for
    FILL_BARS, exhaustion if it fills in that time. Gaps that fit neither are common gaps and skipped.
    """
    hi, lo, c = _sides(df, up)
    sgn = 1 if up else -1
    n = len(c)
    out = []
    for i in range(CONTEXT, n):
        a = atr[i - 1]
        size = lo[i] - hi[i - 1]
        if size < MIN_GAP * a or i in skip:
            continue
        ctx_hi, ctx_lo = hi[i - CONTEXT : i].max(), lo[i - CONTEXT : i].min()
        move = (c[i - 1] - c[i - CONTEXT]) / a
        fill = next((k for k in range(i + 1, min(n, i + 1 + FILL_BARS)) if lo[k] <= hi[i - 1]), None)
        held = fill is None and i + FILL_BARS <= n - 1
        bonus = min(0.1, 0.05 * size / a)
        target = None
        if move <= 2 and lo[i] > ctx_hi and ctx_hi - ctx_lo <= 6 * a:
            if fill is not None:
                continue  # filled straight away: the breakout failed
            name, with_gap, conf = "Breakaway Gap", True, 0.6 + bonus
        elif move >= 3:
            if fill is not None:
                name, with_gap, conf = "Exhaustion Gap", False, 0.6 + bonus
            else:
                # a measuring gap tends to sit halfway through the move
                mid = (hi[i - 1] + lo[i]) / 2
                target = project(sgn * mid, mid - ctx_lo, up)
                name, with_gap, conf = "Runaway Gap", True, 0.55 + bonus
        else:
            continue
        # A gap becomes known once it fills or has held; until then it runs to the last bar.
        end = fill if fill is not None else i + FILL_BARS if held else n - 1
        out.append(Pattern(
            name=name,
            category="gap",
            direction=("bullish" if up else "bearish") if with_gap else ("bearish" if up else "bullish"),
            start=i - 1,
            end=end,
            confidence=round(min(conf + (0.1 if held else 0), 0.95), 2),
            points=[(i - 1, sgn * hi[i - 1]), (i, sgn * lo[i])],
            lines=_box(i - 1, max(end, min(n - 1, i + FILL_BARS)), sgn * hi[i - 1], sgn * lo[i])
            + ([target_segment(end, float(target), n)] if target is not None else []),
            target=round(float(target), 4) if target is not None else None,
            confirmed=fill is not None or held,
            label=LABELS[name],
        ))
    return out


def detect_gaps(df: pd.DataFrame, atr: np.ndarray) -> list[Pattern]:
    tops, used_up = _islands(df, atr, up=True)
    bottoms, used_down = _islands(df, atr, up=False)
    used = used_up | used_down
    return tops + bottoms + _gaps(df, atr, True, used) + _gaps(df, atr, False, used)
