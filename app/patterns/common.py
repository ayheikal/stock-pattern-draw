from __future__ import annotations

import numpy as np

from .models import Pattern, Point


def line_through(p1: Point, p2: Point) -> tuple[float, float]:
    """Slope/intercept of the line through two (index, price) points."""
    (x1, y1), (x2, y2) = p1, p2
    m = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    return m, y1 - m * x1


def segment(m: float, b: float, i0: int, i1: int, n: int) -> list[Point]:
    i0, i1 = max(0, i0), min(n - 1, i1)
    return [(i0, m * i0 + b), (i1, m * i1 + b)]


def project(base: float, height: float, up: bool) -> float:
    """Measured-move target. Large downside moves are projected proportionally so they stay positive."""
    if up:
        return base + height
    return base - height if height < 0.5 * base else base * base / (base + height)


def target_segment(end: int, price: float, n: int, length: int = 15) -> list[Point]:
    """Short horizontal stub marking a price target, kept inside the chart's bars."""
    i1 = min(n - 1, end + length)
    i0 = end if i1 - end >= 3 else max(0, i1 - length)
    return [(i0, price), (i1, price)]


def bowl_curve(left: int, bottom: int, right: int, hl: float, hr: float, lo: float, sgn: int) -> list[Point]:
    """A U through both rims and the bowl's low: a half-parabola on each side of the bottom."""
    pts = []
    for i in sorted({*np.linspace(left, bottom, 8).round().astype(int), *np.linspace(bottom, right, 8).round().astype(int)}):
        rim, half = (hl, bottom - left) if i <= bottom else (hr, right - bottom)
        pts.append((int(i), (lo + (rim - lo) * ((i - bottom) / max(half, 1)) ** 2) * sgn))
    return pts


def find_bowl(high: np.ndarray, low: np.ndarray, atr: np.ndarray, left: int, min_bars: int, max_bars: int
              ) -> tuple[int, int, float, float, float] | None:
    """A rounded U from the high at `left` back up to about that level (mirror prices for an upside-down one).

    Returns (right rim, bottom, depth, share of bars in the bottom third, where the low sits from 0 to 1).
    """
    n = len(high)
    hl, a = high[left], atr[left]
    tol = max(a, 0.02 * abs(hl))
    # Right rim: the first bar that climbs back to the left rim after a real descent.
    dropped = np.flatnonzero(low[left + 1 : left + max_bars] < hl - 3 * a)
    if not len(dropped):
        return None
    d = left + 1 + int(dropped[0])
    back = np.flatnonzero(high[d : left + max_bars] >= hl - tol)
    if not len(back):
        return None
    right = d + int(back[0])
    while right + 1 < n and high[right] <= high[right + 1] <= hl + tol:
        right += 1
    width = right - left
    if width < min_bars or high[left + 1 : right].max() > hl + tol:
        return None
    bottom = left + int(np.argmin(low[left : right + 1]))
    lo = low[bottom]
    depth = (hl + high[right]) / 2 - lo
    if depth < 3 * a or depth > 0.5 * abs(hl):
        return None
    # Rounded U, not a V: a parabola spends ~58% of its width in the bottom third, a V only ~33%.
    rounded = float(np.mean(low[left : right + 1] <= lo + depth / 3))
    pos = (bottom - left) / width
    if rounded < 0.4 or not 0.2 <= pos <= 0.8:
        return None
    return right, bottom, float(depth), rounded, pos


def first_break(close: np.ndarray, start: int, stop: int, m: float, b: float, below: bool) -> int | None:
    """First bar in [start, stop) whose close crosses the line (below or above)."""
    for i in range(start, min(stop, len(close))):
        lvl = m * i + b
        if (below and close[i] < lvl) or (not below and close[i] > lvl):
            return i
    return None


def suppress_overlaps(patterns: list[Pattern], max_overlap: float = 0.5) -> list[Pattern]:
    """Greedy non-max suppression: keep the most confident of overlapping patterns."""
    kept: list[Pattern] = []
    for p in sorted(patterns, key=lambda p: -p.confidence):
        span = p.end - p.start + 1
        ok = True
        for k in kept:
            inter = min(p.end, k.end) - max(p.start, k.start) + 1
            if inter > 0 and inter / min(span, k.end - k.start + 1) > max_overlap:
                ok = False
                break
        if ok:
            kept.append(p)
    return sorted(kept, key=lambda p: p.start)
