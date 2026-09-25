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
