"""Rule-based Japanese candlestick patterns, marked only in a suitable trend context."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .models import Pattern

# (name, label, direction, bars) — longer patterns win when several match one bar, then dict order.
SPECS = {
    "rising_three": ("Rising Three Methods", "R3M", "bullish", 5),
    "falling_three": ("Falling Three Methods", "F3M", "bearish", 5),
    "morning_star": ("Morning Star", "MS", "bullish", 3),
    "evening_star": ("Evening Star", "ES", "bearish", 3),
    "three_outside_up": ("Three Outside Up", "3OU", "bullish", 3),
    "three_outside_down": ("Three Outside Down", "3OD", "bearish", 3),
    "three_inside_up": ("Three Inside Up", "3IU", "bullish", 3),
    "three_inside_down": ("Three Inside Down", "3ID", "bearish", 3),
    "three_soldiers": ("Three White Soldiers", "3WS", "bullish", 3),
    "three_crows": ("Three Black Crows", "3BC", "bearish", 3),
    "bull_engulfing": ("Bullish Engulfing", "BuE", "bullish", 2),
    "bear_engulfing": ("Bearish Engulfing", "BeE", "bearish", 2),
    "piercing": ("Piercing Line", "PL", "bullish", 2),
    "dark_cloud": ("Dark Cloud Cover", "DCC", "bearish", 2),
    "tweezer_bottom": ("Tweezer Bottom", "TwB", "bullish", 2),
    "tweezer_top": ("Tweezer Top", "TwT", "bearish", 2),
    "bull_harami": ("Bullish Harami", "BuH", "bullish", 2),
    "bear_harami": ("Bearish Harami", "BeH", "bearish", 2),
    "dragonfly_doji": ("Dragonfly Doji", "DD", "bullish", 1),
    "gravestone_doji": ("Gravestone Doji", "GD", "bearish", 1),
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
    # For 3- and 5-bar patterns look at the trend before the first candle.
    down3, up3 = _shift(down.astype(float), 2) == 1, _shift(up.astype(float), 2) == 1
    down5, up5 = _shift(down.astype(float), 4) == 1, _shift(up.astype(float), 4) == 1

    o1, c1, b1, h1, l1 = (_shift(x, 1) for x in (o, c, body, h, l))
    o2, c2, b2 = _shift(o, 2), _shift(c, 2), _shift(body, 2)
    o4, c4, b4, h4, l4 = (_shift(x, 4) for x in (o, c, body, h, l))
    bull1, bear1 = c1 > o1, c1 < o1
    big1, big2, big4 = b1 > 0.6 * atr, b2 > 0.6 * atr, b4 > 0.6 * atr

    hammer_shape = (lower >= 2 * body) & (upper <= 0.15 * rng) & (body >= 0.05 * rng)
    star_shape = (upper >= 2 * body) & (lower <= 0.15 * rng) & (body >= 0.05 * rng)
    doji = (body <= 0.08 * rng) & (rng > 0.5 * atr)
    mid1, mid2 = (o1 + c1) / 2, (o2 + c2) / 2

    with np.errstate(invalid="ignore"):
        # Three Methods: three small candles held inside the first one's range.
        held = np.ones(len(c), dtype=bool)
        for k in (1, 2, 3):
            held &= (_shift(h, k) <= h4) & (_shift(l, k) >= l4) & (_shift(body, k) < 0.5 * b4)
        sig = {
            "doji": doji & (up | down),
            "dragonfly_doji": doji & (lower >= 0.7 * rng) & (upper <= 0.1 * rng) & down,
            "gravestone_doji": doji & (upper >= 0.7 * rng) & (lower <= 0.1 * rng) & up,
            "hammer": hammer_shape & down,
            "hanging_man": hammer_shape & up,
            "inverted_hammer": star_shape & down,
            "shooting_star": star_shape & up,
            "bull_engulfing": down & bear1 & bull & (o <= c1) & (c >= o1) & (body > b1),
            "bear_engulfing": up & bull1 & bear & (o >= c1) & (c <= o1) & (body > b1),
            # Opens at or beyond the prior close, then closes past the middle of its body without engulfing it.
            # "At" counts because EGX bars open at the previous close, so they never gap; for the same reason
            # harami and three soldiers / crows may open at the edge of the prior body, not only inside it.
            "piercing": down & bear1 & big1 & bull & (o <= c1) & (c > mid1) & (c < o1),
            "dark_cloud": up & bull1 & big1 & bear & (o >= c1) & (c < mid1) & (c > o1),
            # Matching lows (highs) on a down (up) candle and the next one that turns.
            "tweezer_bottom": down & bear1 & (b1 > 0.3 * atr) & bull & (np.abs(l - l1) <= 0.1 * atr),
            "tweezer_top": up & bull1 & (b1 > 0.3 * atr) & bear & (np.abs(h - h1) <= 0.1 * atr),
            "bull_harami": down & bear1 & big1 & bull & (o >= c1) & (c < o1) & (body < 0.5 * b1),
            "bear_harami": up & bull1 & big1 & bear & (o <= c1) & (c > o1) & (body < 0.5 * b1),
            "morning_star": down3 & (c2 < o2) & big2 & (b1 < 0.35 * b2) & bull & (c > mid2),
            "evening_star": up3 & (c2 > o2) & big2 & (b1 < 0.35 * b2) & bear & (c < mid2),
            # A harami, then a candle that closes past the first one's open.
            "three_inside_up": down3 & (c2 < o2) & big2 & bull1 & (o1 >= c2) & (c1 < o2) & (b1 < 0.5 * b2) & bull & (c > o2),
            "three_inside_down": up3 & (c2 > o2) & big2 & bear1 & (o1 <= c2) & (c1 > o2) & (b1 < 0.5 * b2) & bear & (c < o2),
            # An engulfing pair, then a candle that closes further on.
            "three_outside_up": down3 & (c2 < o2) & bull1 & (o1 <= c2) & (c1 >= o2) & (b1 > b2) & bull & (c > c1),
            "three_outside_down": up3 & (c2 > o2) & bear1 & (o1 >= c2) & (c1 <= o2) & (b1 > b2) & bear & (c < c1),
            "three_soldiers": (c2 > o2) & bull1 & bull & (c1 > c2) & (c > c1)
            & (o1 > o2) & (o1 <= c2) & (o > o1) & (o <= c1) & big & big1 & big2 & (upper < 0.3 * body),
            "three_crows": (c2 < o2) & bear1 & bear & (c1 < c2) & (c < c1)
            & (o1 < o2) & (o1 >= c2) & (o < o1) & (o >= c1) & big & big1 & big2 & (lower < 0.3 * body),
            "rising_three": up5 & (c4 > o4) & big4 & held & bull & big & (c > c4),
            "falling_three": down5 & (c4 < o4) & big4 & held & bear & big & (c < c4),
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
                confidence={1: 0.5, 2: 0.6, 3: 0.7, 5: 0.75}[bars],
                points=[(i, price)],
                label=label,
            )
        )
    return out
