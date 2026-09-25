"""Continuation patterns: triangles, wedges, rectangles, channels, flags and pennants."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pivots import Pivot
from .common import project, segment, target_segment
from .models import Pattern

FLAT = 0.8  # total slope over the pattern below this many ATRs counts as flat


def _fit(points: list[Pivot]) -> tuple[float, float, float]:
    x = np.array([p.idx for p in points], dtype=float)
    y = np.array([p.price for p in points])
    m, b = np.polyfit(x, y, 1)
    return float(m), float(b), float(np.max(np.abs(y - (m * x + b))))


def _classify(du: float, dl: float, conv: bool, par: bool) -> tuple[str, str, str] | None:
    uf, lf = abs(du) < FLAT, abs(dl) < FLAT
    if uf and lf:
        return "Rectangle", "continuation", "neutral"
    if conv:
        if uf and dl > 0:
            return "Ascending Triangle", "continuation", "bullish"
        if lf and du < 0:
            return "Descending Triangle", "continuation", "bearish"
        if du < 0 < dl:
            return "Symmetrical Triangle", "continuation", "neutral"
        if du > 0 and dl > 0:
            return "Rising Wedge", "continuation", "bearish"
        if du < 0 and dl < 0:
            return "Falling Wedge", "continuation", "bullish"
    if par and not uf and not lf and du * dl > 0:
        return ("Ascending Channel", "trend", "bullish") if du > 0 else ("Descending Channel", "trend", "bearish")
    return None


LABELS = {
    "Rectangle": "Rect",
    "Ascending Triangle": "Asc Tri",
    "Descending Triangle": "Desc Tri",
    "Symmetrical Triangle": "Sym Tri",
    "Rising Wedge": "R Wedge",
    "Falling Wedge": "F Wedge",
    "Ascending Channel": "Asc Ch",
    "Descending Channel": "Desc Ch",
}


def detect_geometric(df: pd.DataFrame, piv: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    high, low, close = (df[c].to_numpy() for c in ("High", "Low", "Close"))
    n = len(close)
    out: list[Pattern] = []
    for j in range(4, len(piv)):
        for w in range(5, 9):
            if j - w + 1 < 0:
                break
            win = piv[j - w + 1 : j + 1]
            hs = [p for p in win if p.kind == "H"]
            ls = [p for p in win if p.kind == "L"]
            if len(hs) < 2 or len(ls) < 2:
                continue
            start, end = win[0].idx, win[-1].idx
            length = end - start
            if length < 15:
                continue
            a = float(atr[start : end + 1].mean())
            mu, bu, ru = _fit(hs)
            ml, bl, rl = _fit(ls)
            tol = 0.4 * a
            if ru > tol or rl > tol:
                continue
            w0 = (mu - ml) * start + (bu - bl)
            w1 = (mu - ml) * end + (bu - bl)
            if w0 <= a or w1 <= 0.3 * a:
                continue
            xs = np.arange(start, end + 1)
            viol = np.mean((high[xs] > mu * xs + bu + 0.5 * a) | (low[xs] < ml * xs + bl - 0.5 * a))
            if viol > 0.05:
                continue
            # Both boundaries must begin near the same time, else the shape is incomplete.
            if abs(hs[0].idx - ls[0].idx) > 0.4 * length:
                continue
            du, dl = mu * length / a, ml * length / a
            conv = w1 < 0.75 * w0
            par = 0.8 * w0 <= w1 <= 1.25 * w0
            cls = _classify(du, dl, conv, par)
            if cls is None:
                continue
            name, category, direction = cls

            # Breakout: first close outside the extended boundaries.
            brk, brk_dir = None, None
            horizon = min(n, end + max(10, length // 2))
            for i in range(end + 1, horizon):
                if close[i] > mu * i + bu + 0.1 * a:
                    brk, brk_dir = i, "bullish"
                    break
                if close[i] < ml * i + bl - 0.1 * a:
                    brk, brk_dir = i, "bearish"
                    break
            if brk is None and horizon < n and category != "trend":
                continue  # pattern expired without resolving
            if direction == "neutral" and brk_dir:
                direction = brk_dir
            p_end = brk if brk is not None else (n - 1 if horizon >= n else end)
            if category == "trend" and brk is None:
                p_end = min(n - 1, end + 5)

            target = None
            if category == "continuation" and direction != "neutral":
                target = project(mu * p_end + bu, w0, True) if direction == "bullish" else project(ml * p_end + bl, w0, False)

            touches = len(hs) + len(ls)
            fit_q = 1 - max(ru, rl) / tol
            conf = 0.45 + min(0.2, 0.05 * (touches - 4)) + 0.15 * fit_q + (0.1 if brk is not None else 0)
            if brk_dir and direction != brk_dir and category == "continuation":
                conf -= 0.1  # broke against the expected direction
            out.append(
                Pattern(
                    name=name,
                    category=category,
                    direction=direction,
                    start=start,
                    end=p_end,
                    confidence=round(min(conf, 0.99), 2),
                    points=[(p.idx, p.price) for p in win],
                    lines=[segment(mu, bu, hs[0].idx, p_end, n), segment(ml, bl, ls[0].idx, p_end, n)]
                    + ([target_segment(p_end, float(target), n)] if target is not None else []),
                    target=round(float(target), 4) if target is not None else None,
                    confirmed=brk is not None,
                    label=LABELS[name],
                )
            )
    return out


def _flags_one_side(high, low, close, atr, bull: bool) -> list[Pattern]:
    """Detect bull flags/pennants; the bear side is handled by mirroring prices."""
    n = len(close)
    sgn = 1 if bull else -1
    side = "Bull" if bull else "Bear"
    out = []
    for i in range(12, n - 5):
        if high[i] < high[max(0, i - 3) : i + 4].max():
            continue
        a = atr[i]
        s = i - 12 + int(np.argmin(low[i - 12 : i]))
        pole, bars = high[i] - low[s], i - s
        if bars < 2 or pole < 4 * a or pole / bars < 0.7 * a:
            continue
        m = 0
        for k in range(1, min(21, n - i)):
            j = i + k
            if low[j] < high[i] - 0.5 * pole or high[j] > high[i] + 0.2 * a:
                break
            m = k
        if m < 5:
            continue
        xs = np.arange(i, i + m + 1)
        mu, bu = np.polyfit(xs, high[xs], 1)
        ml, bl = np.polyfit(xs, low[xs], 1)
        bu += float(np.max(high[xs] - (mu * xs + bu)))
        bl -= float(np.max((ml * xs + bl) - low[xs]))
        w0, w1 = (mu - ml) * i + bu - bl, (mu - ml) * (i + m) + bu - bl
        if w0 <= 0 or w1 <= 0 or w0 > 0.6 * pole:
            continue
        du, dl = mu * m / a, ml * m / a
        if w1 < 0.6 * w0:
            kind = "Pennant"
        elif du <= 0.5 and dl <= 0.5:
            kind = "Flag"
        else:
            continue

        end = i + m
        brk = None
        for j in range(end, min(n, end + 10)):
            if close[j] > mu * j + bu:
                brk = j
                break
        if brk is None and end + 10 < n:
            continue  # consolidation never resolved in the pole's direction
        p_end = brk if brk is not None else end
        target = project((mu * p_end + bu) * sgn, pole, up=bull)
        conf = 0.5 + min(0.2, 0.03 * pole / a) + (0.15 if brk is not None else 0)
        name = f"{side} {kind}"
        out.append(
            Pattern(
                name=name,
                category="continuation",
                direction="bullish" if bull else "bearish",
                start=s,
                end=p_end,
                confidence=round(min(conf, 0.99), 2),
                points=[(s, low[s] * sgn), (i, high[i] * sgn)],
                lines=[
                    [(s, low[s] * sgn), (i, high[i] * sgn)],
                    [(i, (mu * i + bu) * sgn), (p_end, (mu * p_end + bu) * sgn)],
                    [(i, (ml * i + bl) * sgn), (p_end, (ml * p_end + bl) * sgn)],
                    target_segment(p_end, float(target), n),
                ],
                target=round(float(target), 4),
                confirmed=brk is not None,
                label=name,
            )
        )
    return out


def detect_flags(df: pd.DataFrame, atr: np.ndarray) -> list[Pattern]:
    high, low, close = (df[c].to_numpy() for c in ("High", "Low", "Close"))
    return _flags_one_side(high, low, close, atr, bull=True) + _flags_one_side(-low, -high, -close, atr, bull=False)
