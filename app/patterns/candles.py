"""Rule-based Japanese candlestick patterns, marked only in a suitable trend context."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .models import Pattern

# (name, label, direction, bars) — longer patterns win when several match one bar.
SPECS = {
    "morning_star": ("Morning Star", "MS", "bullish", 3),
    "evening_star": ("Evening Star", "ES", "bearish", 3),
    "three_soldiers": ("Three White Soldiers", "3WS", "bullish", 3),
    "three_crows": ("Three Black Crows", "3BC", "bearish", 3),
    "bull_engulfing": ("Bullish Engulfing", "BuE", "bullish", 2),
    "bear_engulfing": ("Bearish Engulfing", "BeE", "bearish", 2),
    "bull_harami": ("Bullish Harami", "BuH", "bullish", 2),
    "bear_harami": ("Bearish Harami", "BeH", "bearish", 2),
    "hammer": ("Hammer", "Ham", "bullish", 1),
    "inverted_hammer": ("Inverted Hammer", "IH", "bullish", 1),
    "hanging_man": ("Hanging Man", "HM", "bearish", 1),
    "shooting_star": ("Shooting Star", "SS", "bearish", 1),
    "doji": ("Doji", "D", "neutral", 1),
}


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    out[k:] = x[:-k]
    return out


def candle_signals(df: pd.DataFrame, atr: np.ndarray) -> dict[str, np.ndarray]:
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    bull, bear = c > o, c < o
    big = body > 0.6 * atr

    # Trend context: 5-bar move ending at the previous bar, measured in ATRs.
    move = (_shift(c, 1) - _shift(c, 6)) / atr
    down, up = move < -1.0, move > 1.0
    # For 3-bar patterns look at the trend before the first candle.
    down3, up3 = _shift(down.astype(float), 2) == 1, _shift(up.astype(float), 2) == 1

    o1, c1, b1 = _shift(o, 1), _shift(c, 1), _shift(body, 1)
    o2, c2, b2 = _shift(o, 2), _shift(c, 2), _shift(body, 2)
    bull1, bear1 = c1 > o1, c1 < o1
    big1, big2 = b1 > 0.6 * atr, b2 > 0.6 * atr

    hammer_shape = (lower >= 2 * body) & (upper <= 0.15 * rng) & (body >= 0.05 * rng)
    star_shape = (upper >= 2 * body) & (lower <= 0.15 * rng) & (body >= 0.05 * rng)
    mid2 = (o2 + c2) / 2

    with np.errstate(invalid="ignore"):
        sig = {
            "doji": (body <= 0.08 * rng) & (rng > 0.5 * atr) & (up | down),
            "hammer": hammer_shape & down,
            "hanging_man": hammer_shape & up,
            "inverted_hammer": star_shape & down,
            "shooting_star": star_shape & up,
            "bull_engulfing": down & bear1 & bull & (o <= c1) & (c >= o1) & (body > b1),
            "bear_engulfing": up & bull1 & bear & (o >= c1) & (c <= o1) & (body > b1),
            "bull_harami": down & bear1 & big1 & bull & (o > c1) & (c < o1) & (body < 0.5 * b1),
            "bear_harami": up & bull1 & big1 & bear & (o < c1) & (c > o1) & (body < 0.5 * b1),
            "morning_star": down3 & (c2 < o2) & big2 & (b1 < 0.35 * b2) & bull & (c > mid2),
            "evening_star": up3 & (c2 > o2) & big2 & (b1 < 0.35 * b2) & bear & (c < mid2),
            "three_soldiers": (c2 > o2) & bull1 & bull & (c1 > c2) & (c > c1)
            & (o1 > o2) & (o1 < c2) & (o > o1) & (o < c1) & big & big1 & big2 & (upper < 0.3 * body),
            "three_crows": (c2 < o2) & bear1 & bear & (c1 < c2) & (c < c1)
            & (o1 < o2) & (o1 > c2) & (o < o1) & (o > c1) & big & big1 & big2 & (lower < 0.3 * body),
        }
    return {k: np.nan_to_num(v.astype(float)).astype(bool) for k, v in sig.items()}


def detect_candles(df: pd.DataFrame, atr: np.ndarray, lookback: int = 150) -> list[Pattern]:
    sig = candle_signals(df, atr)
    high, low = df["High"].to_numpy(), df["Low"].to_numpy()
    n = len(df)
    out = []
    for i in range(max(0, n - lookback), n):
        hits = [k for k in SPECS if sig[k][i]]
        if not hits:
            continue
        key = max(hits, key=lambda k: SPECS[k][3])  # dict order breaks ties
        name, label, direction, bars = SPECS[key]
        price = float(low[i]) if direction == "bullish" else float(high[i])
        out.append(
            Pattern(
                name=name,
                category="candle",
                direction=direction,
                start=i - bars + 1,
                end=i,
                confidence={1: 0.5, 2: 0.6, 3: 0.7}[bars],
                points=[(i, price)],
                label=label,
            )
        )
    return out
