"""Indicator values and the crossing signals they give, for the track record.

Worked out as on the chart page (static/app.js): EMAs seeded with a simple average, RSI with Wilder's
smoothing, Bollinger Bands with the population standard deviation, so a signal here is one you'd see there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(c: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(c).rolling(n).mean().to_numpy()


def ema(c: np.ndarray, n: int) -> np.ndarray:
    """Seeded with the simple average of the first n values; NaNs at the start are skipped."""
    out = np.full(len(c), np.nan)
    ok = np.flatnonzero(~np.isnan(c))
    if not len(ok) or len(c) - ok[0] < n:
        return out
    s, k = ok[0], 2 / (n + 1)
    e = c[s : s + n].mean()
    out[s + n - 1] = e
    for i in range(s + n, len(c)):
        e = c[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(len(c), np.nan)
    d = np.diff(c)
    if len(d) < n:
        return out
    gain, loss = np.clip(d, 0, None), np.clip(-d, 0, None)
    g, l = gain[:n].mean(), loss[:n].mean()
    for i in range(n, len(c)):
        if i > n:
            g, l = (g * (n - 1) + gain[i - 1]) / n, (l * (n - 1) + loss[i - 1]) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(c: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[np.ndarray, np.ndarray]:
    line = ema(c, fast) - ema(c, slow)
    return line, ema(line, sig)


def bollinger(c: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    s = pd.Series(c)
    mid, sd = s.rolling(n).mean(), s.rolling(n).std(ddof=0)
    return (mid - k * sd).to_numpy(), (mid + k * sd).to_numpy()


def session_vwap(df: pd.DataFrame) -> np.ndarray:
    """Volume-weighted average price, restarting each trading day (typical price x volume, running).
    NaN until the session has traded any volume (indices never do)."""
    day = df.index.normalize()
    vol = df["Volume"].astype(float)
    pv = (df["High"] + df["Low"] + df["Close"]) / 3 * vol
    cum_v = vol.groupby(day).cumsum()
    return (pv.groupby(day).cumsum() / cum_v.where(cum_v > 0)).to_numpy(dtype=float)


def relative_volume_series(df: pd.DataFrame, sessions: int = 10) -> np.ndarray:
    """At each bar: its session's volume so far, over the average volume by the same time of day in up to
    `sessions` earlier sessions. NaN without volume or an earlier session to compare with."""
    day = df.index.normalize()
    cum = df["Volume"].astype(float).groupby(day).cumsum().to_numpy()
    t = pd.DataFrame({"day": day, "clock": df.index.time, "cum": cum})
    # volume so far by clock time (rows) for each session (columns); a session with no bar at a time keeps its last total
    tbl = t.pivot_table(index="clock", columns="day", values="cum", aggfunc="last").sort_index().ffill()
    # the average over the earlier sessions that had traded by that time (sessions starting later are left out)
    base = tbl.apply(lambda row: row.dropna().shift(1).rolling(sessions, min_periods=1).mean(), axis=1).reindex(columns=tbl.columns)
    b = base.to_numpy(dtype=float)[tbl.index.get_indexer(t["clock"]), tbl.columns.get_indexer(t["day"])]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(b > 0, cum / b, np.nan)


def relative_volume(df: pd.DataFrame, sessions: int = 10) -> float | None:
    """relative_volume_series at the last bar; None where it has no reading."""
    v = relative_volume_series(df, sessions)[-1]
    return None if np.isnan(v) else float(v)


def adx(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    """Wilder's ADX: how strong the trend is (above ~25 trending, below ~20 sideways), whichever way it points."""
    high, low, close = (df[k].to_numpy(dtype=float) for k in ("High", "Low", "Close"))
    up, dn = np.diff(high, prepend=high[0]), -np.diff(low, prepend=low[0])
    plus, minus = np.where((up > dn) & (up > 0), up, 0.0), np.where((dn > up) & (dn > 0), dn, 0.0)
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    wilder = lambda x: pd.Series(x).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    t = wilder(tr)
    pdi, mdi = 100 * wilder(plus) / t, 100 * wilder(minus) / t
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).fillna(0.0).where(t.notna())
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def _up(a: np.ndarray, b: np.ndarray | float) -> np.ndarray:
    """True on the bar where a moves from at-or-below b to above it."""
    b = np.broadcast_to(b, a.shape)
    out = np.zeros(len(a), dtype=bool)
    with np.errstate(invalid="ignore"):
        out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def _down(a: np.ndarray, b: np.ndarray | float) -> np.ndarray:
    return _up(-a, -np.broadcast_to(b, a.shape))


def signal_bars(df: pd.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    """(name, direction) -> bars where that signal fired. Directions are the textbook reading;
    the track record shows whether price actually went that way."""
    c = df["Close"].to_numpy(dtype=float)
    m, s = macd(c)
    r = rsi(c)
    low, high = bollinger(c)
    s50, s200 = sma(c, 50), sma(c, 200)
    return {
        ("MACD crossed above its signal", "bullish"): _up(m, s),
        ("MACD crossed below its signal", "bearish"): _down(m, s),
        ("MACD crossed above zero", "bullish"): _up(m, 0.0),
        ("MACD crossed below zero", "bearish"): _down(m, 0.0),
        ("RSI fell below 30 (oversold)", "bullish"): _down(r, 30.0),
        ("RSI back above 30", "bullish"): _up(r, 30.0),
        ("RSI rose above 70 (overbought)", "bearish"): _up(r, 70.0),
        ("RSI back below 70", "bearish"): _down(r, 70.0),
        ("Close below the lower Bollinger Band", "bullish"): _down(c, low),
        ("Close above the upper Bollinger Band", "bearish"): _up(c, high),
        ("Close back inside from below the lower band", "bullish"): _up(c, low),
        ("Close back inside from above the upper band", "bearish"): _down(c, high),
        ("Golden cross (SMA 50 above SMA 200)", "bullish"): _up(s50, s200),
        ("Death cross (SMA 50 below SMA 200)", "bearish"): _down(s50, s200),
        ("Close crossed above SMA 200", "bullish"): _up(c, s200),
        ("Close crossed below SMA 200", "bearish"): _down(c, s200),
    }


def recent_signals(df: pd.DataFrame, bars: int) -> list[list[str]]:
    """[name, direction] of each signal that fired within the last `bars` bars."""
    return [[name, d] for (name, d), hit in signal_bars(df).items() if hit[-bars:].any()]
