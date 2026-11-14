"""Intraday long setup: five rules on small bars (trend, VWAP, momentum, relative volume, entry candle).

Trend: EMA 8 above EMA 20 and both rising. Position: close above the session VWAP. Momentum: RSI above 50.
Liquidity: relative volume 1.5 or more. Entry: the last bar closed on a breakout or after a retest.
ATR sets the stop and target. Everything is judged on the last completed bar.

`signals` works the rules out at every bar from that bar and earlier ones only, so the live scan (`evaluate`,
its last bar) and the backtest (intraday_backtest.py, every bar) read exactly the same rules.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import indicators
from .data import InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .pivots import atr as compute_atr

INTERVALS = {"5m": "1mo", "15m": "1mo", "1h": "3mo"}  # bar size -> how much history to load
BAR_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
SHOW = {"all5": 5, "4plus": 4, "all": 0}  # the fewest rules a listed row must pass
RULES = {"trend": "Trend", "vwap": "VWAP", "rsi": "RSI", "volume": "Volume", "entry": "Entry"}

RSI_MIN = 50.0
RVOL_MIN = 1.5
SLOPE_BARS = 3       # "rising" = higher than this many bars ago
BREAKOUT_BARS = 20   # a breakout closes above the high of this many earlier bars
RETEST_BARS = 3      # a retest touched VWAP / EMA 20 within this many bars
TOUCH_ATR = 0.2      # ... coming within this many ATRs of it
STOP_ATR, TARGET_ATR = 1.5, 3.0
MIN_BARS = 25


def _completed(df: pd.DataFrame, interval: str, now: pd.Timestamp | None) -> pd.DataFrame:
    """Without the last bar when it's still forming."""
    if now is None:
        now = pd.Timestamp.now(tz=df.index.tz)
    return df.iloc[:-1] if df.index[-1] + pd.Timedelta(minutes=BAR_MINUTES[interval]) > now else df


def _num(x: float | None, digits: int = 4) -> float | None:
    return None if x is None or np.isnan(x) else round(float(x), digits)


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    return np.r_[np.full(k, np.nan), x[:-k]]


def signals(df: pd.DataFrame) -> dict:
    """The five rules at every bar. "ok" maps each rule to a True/False array, "passed" counts them;
    the rest are the readings behind them. A rule with no data (no volume) is False, see has_vwap / rvol."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    e8, e20 = indicators.ema(c, 8), indicators.ema(c, 20)
    vwap = indicators.session_vwap(df)
    r = indicators.rsi(c)
    rvol = indicators.relative_volume_series(df)
    a = compute_atr(df)
    has_vwap = ~np.isnan(vwap)
    with np.errstate(invalid="ignore"):
        rising = (e8 > _lag(e8, SLOPE_BARS)) & (e20 > _lag(e20, SLOPE_BARS))
        above = e8 > e20
        breakout = c > pd.Series(h).rolling(BREAKOUT_BARS).max().shift(1).to_numpy()
        # the closest a low came to EMA 20 / VWAP in the last few bars, against this bar's ATR
        near = pd.Series(l - e20).rolling(RETEST_BARS, min_periods=1).min().to_numpy() <= TOUCH_ATR * a
        near |= pd.Series(l - vwap).rolling(RETEST_BARS, min_periods=1).min().to_numpy() <= TOUCH_ATR * a
        retest = ~breakout & near & (c > o) & (c > e20) & (~has_vwap | (c > vwap))
        ok = {"trend": above & rising, "vwap": has_vwap & (c > vwap), "rsi": r > RSI_MIN,
              "volume": rvol >= RVOL_MIN, "entry": breakout | retest}
    return {"ok": ok, "passed": sum(v.astype(int) for v in ok.values()), "open": o, "high": h, "low": l, "close": c,
            "ema8": e8, "ema20": e20, "above": above, "rising": rising, "vwap": vwap, "has_vwap": has_vwap, "rsi": r,
            "rvol": rvol, "atr": a, "breakout": breakout, "retest": retest, "day": df.index.normalize().asi8}


def trade(s: dict, i: int, day_over: bool = True) -> dict:
    """What a long taken on bar i's signal did: in at the next bar's open, stop STOP_ATR x ATR below, target
    TARGET_ATR x ATR above, out at the session's last close if neither came. A bar that touches both counts
    as the stop. outcome: target / stop / time, or skipped (the signal came on the session's last bar),
    pending (the next bar isn't there yet) or open (still running; only when `day_over` is False)."""
    o, h, l, c, day = s["open"], s["high"], s["low"], s["close"], s["day"]
    j, n = i + 1, len(c)
    if j >= n:
        return {"outcome": "pending"}
    if day[j] != day[i]:
        return {"outcome": "skipped"}
    entry, a = float(o[j]), float(s["atr"][i])
    stop, target = entry - STOP_ATR * a, entry + TARGET_ATR * a
    k, outcome, exit_ = j, None, None
    while k < n and day[k] == day[j]:
        if l[k] <= stop:
            outcome, exit_ = "stop", min(float(o[k]), stop)  # a gap through the stop fills at the open
            break
        if h[k] >= target:
            outcome, exit_ = "target", target
            break
        k += 1
    if outcome is None:
        if k == n and not day_over:
            return {"outcome": "open", "entry": entry, "stop": stop, "target": target, "entryIdx": j}
        k -= 1
        outcome, exit_ = "time", float(c[k])
    return {"outcome": outcome, "entry": entry, "stop": stop, "target": target, "exit": exit_, "entryIdx": j, "exitIdx": k,
            "R": (exit_ - entry) / (entry - stop), "retPct": (exit_ / entry - 1) * 100, "riskPct": (entry - stop) / entry * 100}


def evaluate(df: pd.DataFrame, interval: str, now: pd.Timestamp | None = None) -> dict:
    """The five checks and the ATR stop / target for one symbol's bars."""
    df = _completed(df, interval, now)
    if len(df) < MIN_BARS:
        raise InsufficientHistory("", len(df))
    s = signals(df)
    ok = {k: bool(v[-1]) for k, v in s["ok"].items()}
    last, v, r, a = (float(s[k][-1]) for k in ("close", "vwap", "rsi", "atr"))
    rvol = None if np.isnan(s["rvol"][-1]) else float(s["rvol"][-1])
    has_vwap = bool(s["has_vwap"][-1])
    entry = "breakout" if s["breakout"][-1] else "retest" if s["retest"][-1] else None
    text = {
        "trend": f"EMA 8 {'above' if s['above'][-1] else 'below'} EMA 20, {'both rising' if s['rising'][-1] else 'not both rising'}",
        "vwap": f"Close {(last - v) / v * 100:+.2f}% vs VWAP" if has_vwap else "No volume, so no VWAP",
        "rsi": f"RSI {r:.1f} (needs above {RSI_MIN:.0f})",
        "volume": "No volume to compare" if rvol is None else f"Relative volume {rvol:.2f}× (needs {RVOL_MIN}×)",
        "entry": {"breakout": f"Closed above the {BREAKOUT_BARS}-bar high", "retest": "Closed up after retesting VWAP / EMA 20",
                  None: "No breakout or retest close yet"}[entry],
    }
    no_data = {"vwap": not has_vwap, "volume": rvol is None}
    checks = [{"key": k, "label": label, "ok": None if no_data.get(k) else ok[k], "text": text[k]} for k, label in RULES.items()]
    before = s["close"][s["day"] < s["day"][-1]]
    return {
        "checks": checks,
        "passed": int(s["passed"][-1]),
        "entry": entry,
        "last": _num(last),
        "dayPct": _num((last - before[-1]) / before[-1] * 100, 2) if len(before) else None,
        "vwap": _num(v),
        "vwapPct": _num((last - v) / v * 100, 2) if has_vwap else None,
        "ema8": _num(s["ema8"][-1]),
        "ema20": _num(s["ema20"][-1]),
        "rsi": _num(r, 1),
        "rvol": _num(rvol, 2),
        "atr": _num(a, 6),
        "atrPct": _num(a / last * 100, 2),
        "adx": _num(indicators.adx(df)[-1], 1),
        "stop": _num(last - STOP_ATR * a),
        "target": _num(last + TARGET_ATR * a),
        "lastBar": df.index[-1].strftime("%Y-%m-%d %H:%M"),
        "barTime": df.index[-1].isoformat(),
    }


def _scan_one(symbol: str, interval: str) -> dict:
    try:
        resolved, df, meta = fetch_ohlcv(symbol, interval, INTERVALS[interval])
        return {"symbol": resolved, "query": symbol, "name": meta.get("longName") or meta.get("shortName") or resolved,
                "currency": meta.get("currency"), **evaluate(df, interval)}
    except (SymbolNotFound, InsufficientHistory):
        return {"symbol": symbol, "error": "no data"}
    except Exception as e:  # one bad symbol shouldn't sink the whole scan
        return {"symbol": symbol, "error": str(e)[:80]}


def scan(symbols: list[str], interval: str) -> dict:
    """Judge every symbol; most rules passed first, then the heavier relative volume."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda s: _scan_one(s, interval), symbols))
    ok = sorted((r for r in rows if "error" not in r), key=lambda r: (-r["passed"], -(r["rvol"] or 0)))
    return {"scanned": len(ok), "skipped": [r["symbol"] for r in rows if "error" in r], "results": ok}
