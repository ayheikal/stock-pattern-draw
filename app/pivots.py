"""Swing-point (pivot) detection shared by all chart-pattern detectors."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Pivot:
    idx: int
    price: float
    kind: str  # "H" or "L"


def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    high, low, close = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    a = pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=1).mean().to_numpy()
    # Flat stretches (trading halts) give a zero ATR; floor it so ATR-scaled maths never divides by zero.
    return np.maximum(a, np.abs(close) * 1e-4)


def _local_extrema(values: np.ndarray, order: int, mode: str) -> list[int]:
    s = pd.Series(values)
    win = s.rolling(2 * order + 1, center=True, min_periods=order + 1)
    ext = win.max() if mode == "max" else win.min()
    idx = np.flatnonzero(values == ext.to_numpy())
    # On plateaus keep only the first bar of a run of equal extremes.
    out: list[int] = []
    for i in idx:
        if out and i - out[-1] <= order and values[i] == values[out[-1]]:
            continue
        out.append(int(i))
    return out


def find_pivots(df: pd.DataFrame, order: int = 5, min_swing_atr: float = 1.0) -> list[Pivot]:
    """Alternating high/low pivots; swings smaller than `min_swing_atr` x ATR are dropped."""
    high, low = df["High"].to_numpy(), df["Low"].to_numpy()
    a = atr(df)
    raw = [Pivot(i, float(high[i]), "H") for i in _local_extrema(high, order, "max")]
    raw += [Pivot(i, float(low[i]), "L") for i in _local_extrema(low, order, "min")]
    raw.sort(key=lambda p: (p.idx, p.kind == "H"))

    out: list[Pivot] = []
    for p in raw:
        if not out:
            out.append(p)
            continue
        last = out[-1]
        if p.kind == last.kind:
            more_extreme = p.price > last.price if p.kind == "H" else p.price < last.price
            if more_extreme:
                out[-1] = p
            continue
        if p.idx == last.idx:
            continue
        if abs(p.price - last.price) >= min_swing_atr * a[p.idx]:
            out.append(p)
    return out
