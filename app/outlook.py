"""Plain-language expectation for the next period, derived from detected patterns."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .patterns.models import Level, Pattern
from .pivots import atr as compute_atr

# interval -> (bars ahead, human label)
HORIZONS = {
    "1h": (7, "next trading day"),
    "1d": (5, "next week (≈5 sessions)"),
    "1wk": (4, "next month (≈4 weeks)"),
    "1mo": (3, "next quarter (≈3 months)"),
}
RECENT = 20  # patterns ending within this many bars still influence the outlook
SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.5f}"


def _rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else math.inf
    return float(100 - 100 / (1 + rs))


def _bias_label(score: float) -> str:
    if score >= 1.2:
        return "Bullish"
    if score >= 0.4:
        return "Mildly bullish"
    if score > -0.4:
        return "Neutral"
    if score > -1.2:
        return "Mildly bearish"
    return "Bearish"


def build_outlook(df: pd.DataFrame, patterns: list[Pattern], levels: list[Level], interval: str) -> dict:
    close_s = df["Close"]
    n, last = len(df), float(close_s.iloc[-1])
    a = float(compute_atr(df)[-1])
    bars_ahead, horizon = HORIZONS.get(interval, (5, "next few bars"))

    score = 0.0
    drivers: list[dict] = []
    up_targets: list[float] = []
    down_targets: list[float] = []

    # --- chart patterns still in play ---
    main: tuple[float, Pattern] | None = None
    for p in patterns:
        if p.category == "candle":
            continue
        age = n - 1 - p.end
        if age > (5 if p.category == "trend" else RECENT):
            continue
        sgn = SIGN[p.direction]
        if p.target is not None:
            reached = (sgn > 0 and last >= p.target) or (sgn < 0 and last <= p.target)
            if reached:
                continue
            (up_targets if sgn > 0 else down_targets).append(p.target) if sgn else None
        status = "confirmed" if p.confirmed else ("active" if p.category == "trend" else "forming, not yet confirmed")
        weight = p.confidence * (1 - age / (RECENT + 1)) * (1.0 if p.confirmed or p.category == "trend" else 0.5)
        weight *= 1.0 if p.category == "trend" else 1.5
        score += sgn * weight
        # Classical patterns lead the summary; trendlines only when nothing else is active.
        rank = weight + (0 if p.category == "trend" else 10)
        if sgn and (main is None or rank > main[0]):
            main = (rank, p)
        text = f"{p.name} ({status}, {round(p.confidence * 100)}%)"
        if p.target is not None:
            text += f" — target ≈ {_fmt(p.target)}"
        drivers.append({"text": text, "direction": p.direction, "weight": round(weight, 3)})

    # --- latest candlestick signals ---
    for p in patterns:
        if p.category == "candle" and p.end >= n - 3 and p.direction != "neutral":
            score += SIGN[p.direction] * 0.25 * p.confidence
            drivers.append({"text": f"{p.name} candle (last {n - p.end} bar{'s' if n - p.end > 1 else ''})",
                            "direction": p.direction, "weight": round(0.25 * p.confidence, 3)})

    # --- trend and momentum ---
    trend_note, trend = "", 0
    if n >= 50:
        sma20, sma50 = close_s.rolling(20).mean().iloc[-1], close_s.rolling(50).mean().iloc[-1]
        if last > sma50 and sma20 > sma50:
            score += 0.6
            trend = 1
            trend_note = "Price is above its rising 20/50-bar averages (uptrend)."
            drivers.append({"text": "Uptrend: price above 20 & 50-bar averages", "direction": "bullish", "weight": 0.6})
        elif last < sma50 and sma20 < sma50:
            score -= 0.6
            trend = -1
            trend_note = "Price is below its falling 20/50-bar averages (downtrend)."
            drivers.append({"text": "Downtrend: price below 20 & 50-bar averages", "direction": "bearish", "weight": 0.6})
        else:
            trend_note = "The 20/50-bar averages are mixed, so the trend is sideways."
    # Overbought/oversold readings are normal inside a strong trend, so they count less there.
    rsi = _rsi(close_s)
    if rsi >= 70:
        w = 0.15 if trend > 0 else 0.3
        score -= w
        drivers.append({"text": f"RSI {rsi:.0f}: overbought, pullback risk", "direction": "bearish", "weight": w})
    elif rsi <= 30:
        w = 0.15 if trend < 0 else 0.3
        score += w
        drivers.append({"text": f"RSI {rsi:.0f}: oversold, rebound potential", "direction": "bullish", "weight": w})

    drivers.sort(key=lambda d: -d["weight"])
    for d in drivers:
        d.pop("weight")

    # --- levels & targets ---
    above = sorted(lv.price for lv in levels if lv.price > last * 1.001)
    below = sorted((lv.price for lv in levels if lv.price < last * 0.999), reverse=True)
    resistance = above[0] if above else float(df["High"].iloc[-RECENT:].max())
    support = below[0] if below else float(df["Low"].iloc[-RECENT:].min())
    spread = a * math.sqrt(bars_ahead)
    up_candidates = [t for t in up_targets if t > resistance] + above[1:2]
    down_candidates = [t for t in down_targets if t < support] + below[1:2]
    up_target = min(up_candidates) if up_candidates else max(resistance, last) + spread
    down_target = max(down_candidates) if down_candidates else min(support, last) - spread
    down_target = max(down_target, 0.0)

    bias = _bias_label(score)
    if main:
        mp = main[1]
        if mp.category == "trend" and mp.name.endswith("Line"):
            side = "above an uptrend line" if mp.direction == "bullish" else "below a downtrend line"
            lead = f"No active classical pattern; price is holding {side}."
        else:
            lead = f"Main signal: {mp.name} ({'confirmed' if mp.confirmed else 'still forming'}) points {mp.direction}"
            lead += f" toward ≈ {_fmt(mp.target)}." if mp.target is not None else "."
    else:
        lead = "No active chart pattern, so the prevailing trend and key levels drive the outlook."
    summary = f"{bias} bias. {lead} {trend_note}".strip()

    lo, hi = max(0.0, last - spread), last + spread
    return {
        "bias": bias,
        "score": round(score, 2),
        "horizon": horizon,
        "summary": summary,
        "bullCase": f"A close above {_fmt(resistance)} (resistance) would open the way toward {_fmt(up_target)}.",
        "bearCase": f"A close below {_fmt(support)} (support) would expose {_fmt(down_target)}.",
        "range": {
            "low": round(lo, 4),
            "high": round(hi, 4),
            "text": f"Typical range for the {horizon}: {_fmt(lo)} – {_fmt(hi)} (±{spread / last * 100:.1f}%, based on ATR).",
        },
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "drivers": drivers[:6],
    }
