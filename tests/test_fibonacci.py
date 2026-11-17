"""Fibonacci retracements of the last big swing: the reading, the levels, and the entry / stop / target (no network)."""
from __future__ import annotations

import numpy as np
import pytest

from app import fibonacci


def swing(*legs) -> np.ndarray:
    """A close series through the given prices, 20 bars a leg."""
    return np.concatenate([np.linspace(a, b, 20) for a, b in zip(legs, legs[1:])])


def fib(v, last=None, **kw):
    return fibonacci.retracement(v, v, v[-1] if last is None else last, **kw)


def test_levels_of_a_rise_step_down_from_the_high():
    f = fib(swing(100, 200))
    assert f["direction"] == "up" and (f["low"], f["high"]) == (100.0, 200.0)
    assert {x["ratio"]: x["price"] for x in f["levels"]} == {0.236: 176.4, 0.382: 161.8, 0.5: 150.0, 0.618: 138.2, 0.786: 121.4}
    assert [x["price"] for x in f["extensions"]] == [227.2, 261.8] and f["zone"] == {"low": 138.2, "high": 161.8}


@pytest.mark.parametrize("last,read", [(195, "top"), (170, "shallow"), (161.8, "zone"), (138.2, "zone"), (130, "deep"), (115, "broken")])
def test_where_a_pullback_stands_in_the_rise(last, read):
    v = swing(100, 200, last)
    assert fib(v)["read"] == read


def test_entry_stop_and_target_on_a_rise():
    top = fib(swing(100, 200, 195), buffer=1.0)
    assert top["entry"] == 150.0 and top["stop"] == 120.4 and top["target"] == 200.0   # wait for the 50% dip
    zone = fib(swing(100, 200, 145))
    assert zone["entry"] == 145.0 and zone["stop"] == 121.4 and "38.2–61.8% zone" in zone["text"]
    at_high = fib(swing(100, 200))
    assert at_high["target"] == 227.2                                                 # past the high: the 1.272 extension
    broken = fib(swing(100, 200, 110))
    assert broken["entry"] is broken["stop"] is broken["target"] is None and "failed" in broken["text"]


@pytest.mark.parametrize("last,read", [(110, "falling"), (150, "bounce"), (175, "bounce"), (185, "reclaimed")])
def test_a_fall_measures_the_bounce_and_gives_no_entry(last, read):
    f = fib(swing(200, 100, last))
    assert f["direction"] == "down" and f["read"] == read and f["entry"] is None
    assert {x["ratio"]: x["price"] for x in f["levels"]}[0.618] == 161.8                # bounce levels step up from the low


def test_no_swing_when_flat_short_or_beyond_the_window():
    assert fib(np.full(50, 100.0)) is None
    assert fib(swing(100, 103)) is None                                                 # under 5%: noise
    assert fib(np.array([100.0, 200.0])) is None
    old = np.r_[np.linspace(100, 200, 50), np.full(300, 150.0), np.linspace(150, 153, 10)]
    assert fib(old, lookback=126) is None                                               # the big rise is older than the window


def test_a_move_under_way_when_the_window_opens_is_measured_from_where_it_began():
    v = np.r_[np.linspace(100, 300, 40), np.linspace(300, 150, 150), np.linspace(150, 180, 20)]   # peak 170 bars ago
    f = fib(v, lookback=126)
    assert f["direction"] == "down" and (f["high"], f["low"]) == (300.0, 150.0) and f["highIdx"] == 39
    assert f["read"] == "falling" and f["retracePct"] == 20.0
    assert fibonacci.level(fib(swing(100, 200)), 0.5) == 150.0 and fibonacci.level(None, 0.5) is None
