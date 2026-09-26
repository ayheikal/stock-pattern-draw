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


def _stop(long: bool, entry: float, pattern: Pattern | None, df: pd.DataFrame, a: float, levels_beyond: list[float]) -> tuple[float, str]:
    """Stop-loss just beyond the level that invalidates the idea.

    Prefers the main pattern's last swing (e.g. flag low, right shoulder); falls back to the
    next support/resistance level at least 1 ATR away, then to a 2 x ATR volatility stop.
    """
    sgn = 1 if long else -1
    if pattern is not None and pattern.points and pattern.direction == ("bullish" if long else "bearish"):
        i0 = int(pattern.points[-1][0])
        swing = float(df["Low"].iloc[i0:].min()) if long else float(df["High"].iloc[i0:].max())
        stop = swing - sgn * 0.25 * a
        if 0.5 * a <= sgn * (entry - stop) <= 4 * a:
            return stop, f"just {'below' if long else 'above'} the last swing of the {pattern.name}"
    for lvl in levels_beyond:
        stop = lvl - sgn * 0.5 * a
        if a <= sgn * (entry - stop) <= 5 * a:
            return stop, f"just {'below support' if long else 'above resistance'} at {_fmt(lvl)}"
    return entry - sgn * 2 * a, "2 × ATR volatility stop"


def _plan(long: bool, entry: float, target: float, stop: float, label: str, basis: str, conditional: bool) -> dict:
    risk = abs(entry - stop)
    if (target - entry) * (1 if long else -1) <= 0:  # target must lie in the trade's direction
        target = entry + (2 * risk if long else -2 * risk)
    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0
    return {
        "side": "long" if long else "short",
        "label": label,
        "conditional": conditional,
        "entry": round(entry, 4),
        "target": round(target, 4),
        "stop": round(max(stop, 0.0), 4),
        "targetPct": round((target - entry) / entry * 100, 2),
        "stopPct": round((stop - entry) / entry * 100, 2),
        "rr": round(rr, 2),
        "stopBasis": basis,
        "warning": "Reward/risk is below 1:1, so the setup is unattractive." if rr < 1 else None,
    }


def _advice(action: str, tone: str, text: str) -> dict:
    return {"action": action, "tone": tone, "text": text}


def _decision(score: float, last: float, stop: float, support: float, resistance: float,
              up_target: float, down_target: float, sma200: float | None) -> dict:
    """What to do for someone already holding (in profit or at a loss) and for a newcomer, long-only."""
    s, S, R, T, D = _fmt(stop), _fmt(support), _fmt(resistance), _fmt(up_target), _fmt(down_target)
    if score >= 1.2:
        profit = _advice("Hold", "good", f"Let the profit run. Raise your stop to {s} and consider taking some profit near {T}.")
        loss = _advice("Hold", "good", f"The chart supports a recovery toward {T}. Keep a stop at {s}; exit if price closes below it.")
        new = _advice("Buy", "good", f"Setup is bullish. Buy near {_fmt(last)} (or on a dip toward {S}), stop {s}, target {T}.")
    elif score >= 0.4:
        profit = _advice("Hold, tighten stop", "good", f"Keep the position but move your stop up to {s} to protect the gain.")
        loss = _advice("Hold with stop", "caution", f"Signals lean positive, but don't add yet. Exit on a close below {s}; add only after a close above {R}.")
        new = _advice("Buy a starter position", "good", f"Start with part of your intended size (e.g. half), stop {s}. Add the rest on a close above {R}.")
    elif score > -0.4:
        profit = _advice("Hold, take partial profit", "caution", f"No clear direction. Consider locking in part of the gain near {R}; exit the rest on a close below {S}.")
        loss = _advice("Don't average down", "caution", f"No clear edge to recover. Hold only while price stays above {S}; exit on a close below it.")
        new = _advice("Wait", "caution", f"No edge right now. Buy only after a close above {R}; stay out if it breaks below {S}.")
    elif score > -1.2:
        profit = _advice("Take partial profit", "caution", f"Momentum is fading. Take part of the gain (e.g. 1/3–1/2) and keep a stop at {s} on the rest.")
        loss = _advice("Reduce / cut", "bad", f"Signals lean negative with downside toward {D}. Consider reducing now; exit fully on a close below {S}. Don't average down.")
        new = _advice("Don't buy yet", "bad", f"Signals lean negative. Wait for a close above {R} or for price to stabilise near {D}.")
    else:
        profit = _advice("Sell / take profit", "bad", f"The chart has turned bearish, with downside toward {D}. Lock in the gain; re-buy only after a close above {R}.")
        loss = _advice("Cut the loss", "bad", f"The chart is bearish, with downside toward {D}. Exiting limits further damage; re-evaluate after a close above {R}.")
        new = _advice("Stay out", "bad", f"Bearish setup. Don't buy until price closes back above {R}.")

    long_term = None
    if sma200 is not None:
        above = last >= sma200
        dist = (last - sma200) / sma200 * 100
        if above and score <= -0.4:
            long_term = f"Price is still {dist:.1f}% above its 200-bar average ({_fmt(sma200)}), so the long-term uptrend is intact; a close below it would confirm weakness."
        elif not above and score >= 0.4:
            long_term = f"Price is {-dist:.1f}% below its 200-bar average ({_fmt(sma200)}), so the long-term trend is still down: keep position sizes small."
        else:
            long_term = f"Price is {abs(dist):.1f}% {'above' if above else 'below'} its 200-bar average ({_fmt(sma200)}), in line with the {'long-term uptrend' if above else 'long-term downtrend'}."
    return {"stop": round(stop, 4), "holder": {"profit": profit, "loss": loss}, "newcomer": new, "longTerm": long_term}


def build_outlook(df: pd.DataFrame, patterns: list[Pattern], levels: list[Level], interval: str, sma200: float | None = None) -> dict:
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

    # --- trade plan: entry, target, stop-loss ---
    mp = main[1] if main else None
    plans = []
    if score >= 0.4:
        stop, basis = _stop(True, last, mp, df, a, below)
        plans.append(_plan(True, last, up_target, stop, "Buy (bullish bias)", basis, False))
    elif score <= -0.4:
        stop, basis = _stop(False, last, mp, df, a, above)
        plans.append(_plan(False, last, down_target, stop, "Sell / short (bearish bias)", basis, False))
    else:
        # No clear edge: wait for price to leave the range, then trade the break.
        plans.append(_plan(True, resistance, up_target, resistance - 1.5 * a,
                           f"Buy only on a close above {_fmt(resistance)}", "1.5 × ATR below the breakout level", True))
        plans.append(_plan(False, support, down_target, support + 1.5 * a,
                           f"Sell only on a close below {_fmt(support)}", "1.5 × ATR above the breakdown level", True))

    lo, hi = max(0.0, last - spread), last + spread
    hold_stop, _ = _stop(True, last, mp, df, a, below)
    if sma200 is None and n >= 200:
        sma200 = float(close_s.rolling(200).mean().iloc[-1])
    return {
        "plans": plans,
        "decision": _decision(score, last, hold_stop, support, resistance, up_target, down_target, sma200),
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
        "upTarget": round(up_target, 4),
        "downTarget": round(down_target, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "drivers": drivers[:6],
    }
