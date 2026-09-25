from __future__ import annotations

import pandas as pd

from ..pivots import atr as compute_atr
from ..pivots import find_pivots
from .candles import detect_candles
from .common import suppress_overlaps
from .continuation import detect_flags, detect_geometric
from .levels import detect_levels, detect_trendlines
from .models import Level, Pattern
from .reversal import detect_reversals

__all__ = ["analyze", "Pattern", "Level"]


def analyze(df: pd.DataFrame, min_confidence: float = 0.55) -> tuple[list[Pattern], list[Level]]:
    """Run every detector on an OHLCV frame (columns Open/High/Low/Close/Volume)."""
    a = compute_atr(df)
    # Pivot sensitivity scales mildly with history length.
    order = 3 if len(df) < 150 else 5 if len(df) < 600 else 8
    piv = find_pivots(df, order=order, min_swing_atr=1.0)

    reversals = suppress_overlaps([p for p in detect_reversals(df, piv, a) if p.confidence >= min_confidence])
    continuation = [p for p in detect_geometric(df, piv, a) + detect_flags(df, a) if p.confidence >= min_confidence]
    trend = [p for p in continuation if p.category == "trend"] + detect_trendlines(df, piv, a)
    continuation = suppress_overlaps([p for p in continuation if p.category == "continuation"])
    trend = suppress_overlaps(trend, max_overlap=0.7)
    candles = detect_candles(df, a)

    patterns = sorted(reversals + continuation + trend + candles, key=lambda p: (p.start, p.end))
    return patterns, detect_levels(df, piv, a)
