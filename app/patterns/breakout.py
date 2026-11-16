"""What happened at and after a pattern's breakout: the volume behind it, and whether it has since failed.

Measured on EGX daily history (backtest of each pattern's target vs its stop):
- breakouts on 1.8x+ their 20-bar average volume reached the target first ~47% of the time, those on
  under 1.2x ~34%;
- when price had closed back behind its pre-breakout close, only ~24% did (69% hit the stop first).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .models import Pattern

VOLUME_BARS = 20
BREAKOUT_CATEGORIES = ("reversal", "continuation")  # gaps carry their own fill logic; trendlines don't break out


def _breakout(p: Pattern) -> bool:
    return p.confirmed and p.category in BREAKOUT_CATEGORIES and p.direction != "neutral" and p.end >= 1


def weigh_volume(patterns: list[Pattern], df: pd.DataFrame) -> None:
    """Record each breakout's relative volume and fold it into confidence: about +0.06 at 2x, -0.05 at half."""
    vol = df["Volume"].to_numpy(dtype=float) if "Volume" in df else None
    if vol is None or not np.nansum(vol) > 0:
        return  # indices and some feeds carry no volume
    for p in patterns:
        if not _breakout(p):
            continue
        prior = vol[max(0, p.end - VOLUME_BARS) : p.end]
        avg = float(np.nanmean(prior)) if len(prior) else 0.0
        if not avg > 0 or not np.isfinite(vol[p.end]):
            continue
        p.volume_ratio = round(float(vol[p.end]) / avg, 2)
        if p.volume_ratio > 0:
            adj = min(0.1, max(-0.05, 0.06 * math.log2(p.volume_ratio)))
            p.confidence = round(min(0.99, max(0.0, p.confidence + adj)), 2)


def mark_failures(patterns: list[Pattern], df: pd.DataFrame) -> None:
    """Flag breakouts that price has undone: the last close is back behind the close before the breakout bar."""
    close = df["Close"].to_numpy(dtype=float)
    last = len(close) - 1
    for p in patterns:
        if not _breakout(p) or p.end >= last:
            continue
        sgn = 1 if p.direction == "bullish" else -1
        if sgn * (close[last] - close[p.end - 1]) < 0:
            p.failed = True
            p.confidence = round(max(0.0, p.confidence - 0.15), 2)
