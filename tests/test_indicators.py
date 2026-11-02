from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import indicators


def frame(closes) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": 1e6},
                        index=pd.bdate_range("2020-01-01", periods=len(c)))


WALK = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 600))


def test_values_match_the_textbook_formulas():
    c = WALK
    # an SMA-seeded EMA forgets its seed: after 600 bars it equals pandas' recursive EMA
    assert indicators.ema(c, 20)[-1] == pytest.approx(pd.Series(c).ewm(span=20, adjust=False).mean().iloc[-1], rel=1e-9)
    assert np.isnan(indicators.ema(c, 20)[18]) and indicators.ema(c, 20)[19] == pytest.approx(c[:20].mean())
    lo, hi = indicators.bollinger(c)
    mid, sd = c[-20:].mean(), c[-20:].std()
    assert (lo[-1], hi[-1]) == (pytest.approx(mid - 2 * sd), pytest.approx(mid + 2 * sd))
    # Wilder's RSI, written out the long way
    d = np.diff(c)
    g, l = np.clip(d, 0, None)[:14].mean(), np.clip(-d, 0, None)[:14].mean()
    for x in d[14:]:
        g, l = (g * 13 + max(x, 0)) / 14, (l * 13 + max(-x, 0)) / 14
    assert indicators.rsi(c)[-1] == pytest.approx(100 - 100 / (1 + g / l))
    m, s = indicators.macd(c)
    assert m[-1] == pytest.approx(indicators.ema(c, 12)[-1] - indicators.ema(c, 26)[-1])
    assert np.isnan(s[25 + 7]) and not np.isnan(s[25 + 8])  # the signal line needs 9 MACD values


def test_crosses_fire_once_on_the_crossing_bar():
    c = np.r_[np.linspace(100, 150, 30), np.linspace(150, 100, 200), np.linspace(100, 140, 40)]  # a rise, a long slide, then a rally through the SMA 200
    bars = indicators.signal_bars(frame(c))
    up = np.flatnonzero(bars[("Close crossed above SMA 200", "bullish")])
    s200 = indicators.sma(c, 200)
    assert len(up) == 1 and c[up[0]] > s200[up[0]] and c[up[0] - 1] <= s200[up[0] - 1]
    assert not bars[("Close crossed below SMA 200", "bearish")].any()  # it only ever ran below until then
    assert bars[("RSI fell below 30 (oversold)", "bullish")].any() and bars[("RSI back above 30", "bullish")].any()
    assert bars[("MACD crossed above zero", "bullish")].any()


def test_recent_signals_only_look_at_the_last_bars():
    c = np.r_[np.linspace(100, 150, 30), np.linspace(150, 100, 200), np.linspace(100, 140, 40)]
    df = frame(c)
    hit = int(np.flatnonzero(indicators.signal_bars(df)[("Close crossed above SMA 200", "bullish")])[0])
    name = ["Close crossed above SMA 200", "bullish"]
    assert name in indicators.recent_signals(df.iloc[: hit + 1], 5)
    assert name in indicators.recent_signals(df.iloc[: hit + 5], 5)
    assert name not in indicators.recent_signals(df.iloc[: hit + 6], 5)
    assert name not in indicators.recent_signals(df.iloc[:hit], 5)
