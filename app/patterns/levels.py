"""Horizontal support/resistance levels and active trendlines."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pivots import Pivot
from .common import line_through, segment
from .models import Level, Pattern


def detect_levels(df: pd.DataFrame, piv: list[Pivot], atr: np.ndarray, max_levels: int = 6) -> list[Level]:
    """Cluster pivot prices; levels touched more often and more recently rank higher."""
    if not piv:
        return []
    close = df["Close"].to_numpy()
    n, last = len(close), float(close[-1])
    tol = 0.6 * float(np.median(atr[-100:]))
    pts = sorted(piv, key=lambda p: p.price)

    clusters: list[list[Pivot]] = [[pts[0]]]
    for p in pts[1:]:
        if p.price - np.mean([q.price for q in clusters[-1]]) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    levels = []
    for c in clusters:
        if len(c) < 2:
            continue
        price = float(np.mean([p.price for p in c]))
        idxs = [p.idx for p in c]
        recency = max(idxs) / n
        strength = len(c) + 2 * recency
        levels.append(
            Level(
                price=round(price, 4),
                kind="support" if price < last else "resistance",
                touches=len(c),
                first=min(idxs),
                last=max(idxs),
                strength=round(strength, 2),
            )
        )
    # Prefer strong levels near the current price.
    levels.sort(key=lambda lv: -lv.strength + abs(lv.price - last) / (10 * tol))
    return sorted(levels[:max_levels], key=lambda lv: lv.price)


def _best_trendline(piv: list[Pivot], close: np.ndarray, atr: np.ndarray, kind: str) -> Pattern | None:
    n = len(close)
    pts = [p for p in piv if p.kind == kind][-10:]
    up = kind == "L"
    best, best_score = None, 0.0
    for a_i in range(len(pts)):
        for b_i in range(a_i + 1, len(pts)):
            p1, p2 = pts[a_i], pts[b_i]
            if (p2.price > p1.price) != up or p2.idx - p1.idx < 10:
                continue
            m, b = line_through((p1.idx, p1.price), (p2.idx, p2.price))
            a = float(np.median(atr[p1.idx :]))
            xs = np.arange(p1.idx, n)
            line = m * xs + b
            dev = (line - close[xs]) if up else (close[xs] - line)
            if np.any(dev > 0.5 * a):
                continue  # line has been broken
            touches = sum(1 for p in pts if p.idx >= p1.idx and abs(p.price - (m * p.idx + b)) <= 0.35 * a)
            if touches < 3:
                continue
            score = touches + (n - p1.idx) / n
            if score > best_score:
                best_score, best = score, (p1, m, b, touches, a)
    if best is None:
        return None
    p1, m, b, touches, a = best
    touch_pts = [(p.idx, p.price) for p in pts if p.idx >= p1.idx and abs(p.price - (m * p.idx + b)) <= 0.35 * a]
    return Pattern(
        name="Uptrend Line" if up else "Downtrend Line",
        category="trend",
        direction="bullish" if up else "bearish",
        start=p1.idx,
        end=n - 1,
        confidence=round(min(0.99, 0.5 + 0.1 * touches), 2),
        points=touch_pts,
        lines=[segment(m, b, p1.idx, n - 1, n)],
        label="Uptrend" if up else "Downtrend",
    )


def detect_trendlines(df: pd.DataFrame, piv: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    close = df["Close"].to_numpy()
    return [t for t in (_best_trendline(piv, close, atr, "L"), _best_trendline(piv, close, atr, "H")) if t]
