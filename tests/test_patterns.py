from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.patterns import analyze
from app.patterns.candles import candle_signals
from app.pivots import atr, find_pivots


def make_ohlc(waypoints: list[tuple[int, float]], noise: float = 0.3, seed: int = 1) -> pd.DataFrame:
    """Candles following a piecewise-linear path through (bars_to_reach, price) waypoints."""
    rng = np.random.default_rng(seed)
    path = [waypoints[0][1]]
    for bars, price in waypoints[1:]:
        path.extend(np.linspace(path[-1], price, bars + 1)[1:])
    close = np.array(path) + rng.normal(0, noise, len(path))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, noise, len(path))) + 0.2
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    idx = pd.bdate_range("2024-01-01", periods=len(path))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1e6}, index=idx)


def names(df: pd.DataFrame) -> set[str]:
    patterns, _ = analyze(df)
    return {p.name for p in patterns}


LEAD_UP = [(0, 70.0), (25, 100.0)]
LEAD_DOWN = [(0, 160.0), (25, 130.0)]


@pytest.mark.parametrize(
    "path, expected",
    [
        (LEAD_UP + [(10, 120), (8, 110), (10, 132), (10, 110), (8, 120), (12, 95), (10, 90)], "Head and Shoulders"),
        (LEAD_DOWN + [(10, 110), (8, 120), (10, 98), (10, 120), (8, 110), (12, 135), (10, 140)], "Inverse Head and Shoulders"),
        (LEAD_UP + [(12, 120), (10, 105), (12, 120), (12, 95), (10, 90)], "Double Top"),
        (LEAD_DOWN + [(12, 110), (10, 125), (12, 110), (12, 135), (10, 140)], "Double Bottom"),
        (LEAD_UP + [(10, 120), (8, 106), (8, 120), (8, 106), (8, 120), (12, 95)], "Triple Top"),
        (LEAD_DOWN + [(10, 110), (8, 124), (8, 110), (8, 124), (8, 110), (12, 135)], "Triple Bottom"),
    ],
)
def test_reversal_patterns(path, expected):
    assert expected in names(make_ohlc(path))


@pytest.mark.parametrize(
    "path, expected",
    [
        (LEAD_UP + [(8, 120), (6, 104), (6, 120), (6, 109), (6, 120), (5, 114), (5, 120), (8, 135)], "Ascending Triangle"),
        (LEAD_DOWN + [(8, 110), (6, 126), (6, 110), (6, 121), (6, 110), (5, 116), (5, 110), (8, 95)], "Descending Triangle"),
        (LEAD_UP + [(8, 130), (7, 102), (7, 126), (6, 106), (6, 122), (5, 110), (5, 118), (8, 135)], "Symmetrical Triangle"),
        (LEAD_UP + [(8, 115), (6, 100), (6, 115), (6, 100), (6, 115), (6, 100), (6, 115), (10, 130)], "Rectangle"),
        (LEAD_UP + [(6, 108), (6, 102), (7, 112), (7, 98), (8, 116), (8, 94), (9, 120), (9, 92), (10, 135)], "Broadening Formation"),
        (LEAD_UP + [(6, 106), (6, 103), (7, 112), (7, 105), (8, 120), (8, 107), (9, 128), (9, 109), (10, 95)], "Ascending Broadening Wedge"),
        (LEAD_DOWN + [(6, 126), (6, 129), (7, 120), (7, 127), (8, 112), (8, 125), (9, 104), (9, 123), (10, 140)], "Descending Broadening Wedge"),
    ],
)
def test_geometric_patterns(path, expected):
    assert expected in names(make_ohlc(path))


def test_broadening_breaks_either_way_and_targets_its_widest_point():
    up = LEAD_UP + [(6, 108), (6, 102), (7, 112), (7, 98), (8, 116), (8, 94), (9, 120), (9, 92), (10, 135)]
    down = LEAD_DOWN + [(6, 132), (6, 126), (7, 136), (7, 122), (8, 140), (8, 118), (9, 144), (9, 116), (10, 100)]
    for path, direction in ((up, "bullish"), (down, "bearish")):
        p = next(p for p in analyze(make_ohlc(path))[0] if p.name == "Broadening Formation")
        assert p.direction == direction and p.confirmed
        top, bottom = p.lines[0][-1][1], p.lines[1][-1][1]
        assert p.target == pytest.approx(top + (top - bottom) if direction == "bullish" else bottom - (top - bottom), rel=0.15)


def test_bull_and_bear_flags():
    bull = make_ohlc([(0, 100.0), (30, 101), (6, 130), (12, 124), (8, 140), (10, 142)], noise=0.25)
    assert "Bull Flag" in names(bull) or "Bull Pennant" in names(bull)
    bear = make_ohlc([(0, 140.0), (30, 139), (6, 110), (12, 116), (8, 100), (10, 98)], noise=0.25)
    assert "Bear Flag" in names(bear) or "Bear Pennant" in names(bear)


CUP = [(8, 92), (8, 86), (8, 83), (8, 82), (8, 83), (8, 86), (8, 92), (8, 100), (6, 94), (5, 96), (8, 110)]


def test_cup_and_handle():
    bull = make_ohlc(LEAD_UP + CUP)
    assert "Cup and Handle" in names(bull)
    bear = make_ohlc([(0, 130.0), (25, 100.0)] + [(b, 200 - p) for b, p in CUP])
    assert "Inverted Cup and Handle" in names(bear)


def test_v_shape_is_not_a_cup():
    v = make_ohlc(LEAD_UP + [(32, 82), (32, 100), (6, 94), (5, 96), (8, 110)])
    assert "Cup and Handle" not in names(v)


def test_support_resistance_levels():
    path = [(0, 100.0)] + [(8, 120) if k % 2 == 0 else (8, 100) for k in range(12)]
    _, levels = analyze(make_ohlc(path))
    prices = [lv.price for lv in levels]
    assert any(abs(p - 120) < 2 for p in prices)
    assert any(abs(p - 100) < 2 for p in prices)


def _candles(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Declining lead-in followed by explicitly specified (o, h, l, c) candles."""
    lead = [(100 - 2 * i, 100.5 - 2 * i, 97.5 - 2 * i, 98 - 2 * i) for i in range(20)]
    o, h, l, c = zip(*(lead + rows))
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1.0}, index=pd.bdate_range("2024-01-01", periods=len(o)))


def test_candlestick_signals():
    hammer = _candles([(60.0, 60.8, 55.0, 60.7)])
    assert candle_signals(hammer, atr(hammer))["hammer"][-1]

    engulf = _candles([(61.0, 61.2, 58.5, 59.0), (58.5, 62.5, 58.2, 62.0)])
    assert candle_signals(engulf, atr(engulf))["bull_engulfing"][-1]


def test_pivots_alternate():
    df = make_ohlc(LEAD_UP + [(10, 120), (8, 110), (10, 132), (10, 110)])
    piv = find_pivots(df, order=3)
    kinds = [p.kind for p in piv]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_api_analyze(monkeypatch):
    df = make_ohlc(LEAD_UP + [(12, 120), (10, 105), (12, 120), (12, 95), (10, 90)])
    df.index = df.index.tz_localize("America/New_York")
    monkeypatch.setattr(main, "fetch_ohlcv", lambda s, i, p: ("TEST", df, {"longName": "Test Co", "currency": "USD"}))
    client = TestClient(main.app)
    r = client.get("/api/analyze", params={"symbol": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Test Co" and len(body["candles"]) == len(df)
    assert body["candles"][0]["time"] == "2024-01-01"
    assert any(p["name"] == "Double Top" for p in body["patterns"])
    for p in body["patterns"]:
        for ln in p["lines"]:
            assert all(0 <= pt["index"] < len(df) for pt in ln)

    assert client.get("/api/analyze", params={"symbol": "x", "interval": "5m"}).status_code == 400


def test_downside_targets_stay_positive():
    from app.patterns.common import project

    assert project(100, 30, up=False) == 70
    assert 0 < project(100, 150, up=False) < 100
    assert project(100, 150, up=True) == 250


@pytest.mark.parametrize(
    "typed, first",
    [
        ("EGX 33 Shariah", ("tv", "EGX:SHARIAH")),
        ("egx33", ("tv", "EGX:SHARIAH")),
        ("^SHARIAH.CA", ("tv", "EGX:SHARIAH")),
        ("EGX30", ("tv", "EGX:EGX30")),
        ("egx:egx70ewi", ("tv", "EGX:EGX70EWI")),
        ("AAPL", ("yahoo", "AAPL")),
        ("SPX", ("yahoo", "^GSPC")),
    ],
)
def test_symbol_routing(typed, first):
    from app.data import candidates

    assert candidates(typed)[0] == first


def test_egx_stock_prefers_tradingview():
    from app.data import candidates

    assert candidates("COMI.CA") == [("tv", "EGX:COMI"), ("yahoo", "COMI.CA")]
    assert candidates("COMI")[:2] == [("yahoo", "COMI"), ("tv", "EGX:COMI")]


def _outlook(df, interval="1d"):
    from app.outlook import build_outlook

    patterns, levels = analyze(df)
    return build_outlook(df, patterns, levels, interval)


def test_outlook_bearish_after_double_top():
    o = _outlook(make_ohlc(LEAD_UP + [(12, 120), (10, 105), (12, 120), (12, 95), (6, 92)]))
    assert "bearish" in o["bias"].lower()
    assert o["support"] < o["resistance"]
    assert o["range"]["low"] < o["range"]["high"]


def test_outlook_bullish_in_uptrend():
    o = _outlook(make_ohlc([(0, 50.0), (40, 70), (10, 66), (30, 90), (8, 86), (20, 100)]), "1wk")
    assert "bullish" in o["bias"].lower()
    assert "next month" in o["horizon"]


def test_outlook_trade_plan_is_consistent():
    for path, interval in [
        (LEAD_UP + [(12, 120), (10, 105), (12, 120), (12, 95), (6, 92)], "1d"),
        ([(0, 50.0), (40, 70), (10, 66), (30, 90), (8, 86), (20, 100)], "1wk"),
    ]:
        for plan in _outlook(make_ohlc(path), interval)["plans"]:
            if plan["side"] == "long":
                assert plan["stop"] < plan["entry"] < plan["target"]
            else:
                assert plan["target"] < plan["entry"] < plan["stop"]
            assert plan["rr"] > 0
