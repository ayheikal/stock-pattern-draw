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


SAUCER = [(5, 136), (8, 124), (8, 117), (8, 114), (8, 113), (8, 114), (8, 117), (8, 124), (8, 136), (8, 146)]


def test_rounding_bottom_and_top():
    df = make_ohlc(LEAD_DOWN + SAUCER)
    p = next(p for p in analyze(df)[0] if p.name == "Rounding Bottom")
    assert p.direction == "bullish" and p.confirmed and p.category == "reversal"
    rim = p.neckline[0][1]
    assert df["Close"].iloc[p.end] > rim >= df["Close"].iloc[p.end - 1] - 0.5  # ends at the close above the rim
    assert p.target == pytest.approx(rim + (rim - p.points[1][1]), rel=0.05)
    assert "Cup and Handle" not in names(df)

    top = make_ohlc([(0, 140.0), (25, 170.0)] + [(b, 300 - x) for b, x in SAUCER])
    assert "Rounding Top" in names(top)


def test_rounding_needs_a_decline_and_a_rounded_base():
    after_rally = make_ohlc(LEAD_UP + SAUCER)  # price rallied into the rim: a base, not a reversal
    assert "Rounding Bottom" not in names(after_rally)
    v = make_ohlc(LEAD_DOWN + [(5, 136), (36, 113), (36, 136), (8, 146)])
    assert "Rounding Bottom" not in names(v)


DIAMOND = [(5, 106), (5, 101), (6, 111), (6, 96), (7, 116), (7, 93), (6, 110), (6, 99), (5, 106), (5, 102), (10, 85)]


def test_diamond_top_and_bottom():
    p = next(p for p in analyze(make_ohlc(LEAD_UP + DIAMOND))[0] if p.name == "Diamond Top")
    assert p.direction == "bearish" and p.confirmed
    peak, trough = max(v for _, v in p.points), min(v for _, v in p.points)
    assert peak == pytest.approx(116, abs=1.5) and trough == pytest.approx(93, abs=1.5)
    lower_right = p.lines[3][-1][1]  # the edge it broke
    assert p.target == pytest.approx(lower_right - (peak - trough), rel=1e-3)

    bottom = make_ohlc([(0, 130.0), (25, 100.0)] + [(b, 200 - x) for b, x in DIAMOND])
    assert "Diamond Bottom" in names(bottom)


def test_diamond_needs_narrow_ends():
    # widening all the way to the break is a broadening formation, not a diamond
    wide = make_ohlc(LEAD_UP + [(5, 106), (5, 101), (6, 111), (6, 96), (7, 116), (7, 93), (7, 118), (7, 91), (10, 80)])
    assert not any(n.startswith("Diamond") for n in names(wide))


LEAD_IN = [(0, 50.0), (8, 56), (4, 54), (8, 60), (4, 58), (8, 64), (4, 62)]  # a steady climb: lows 54, 58, 62


def test_bump_and_run():
    df = make_ohlc(LEAD_IN + [(10, 85), (14, 58), (6, 55)])
    p = next(p for p in analyze(df)[0] if p.name == "Bump-and-Run Top")
    assert p.direction == "bearish" and p.confirmed
    start = p.points[1][1]  # where the bump left the trendline
    assert p.target == pytest.approx(start) and df["Close"].iloc[p.end] > p.target
    assert max(df["High"]) == pytest.approx(p.points[-1][1])

    bottom = make_ohlc([(b, 150 - x) for b, x in LEAD_IN + [(10, 85), (14, 58), (6, 55)]])
    assert "Bump-and-Run Bottom" in names(bottom)


def test_no_bump_without_a_steep_climb():
    steady = make_ohlc(LEAD_IN + [(8, 68), (4, 66), (8, 72), (14, 58)])  # same pace, then a break
    assert not any(n.startswith("Bump") for n in names(steady))


MEASURED = [(0, 100.0), (10, 99), (15, 125), (10, 112), (15, 140)]


def test_measured_move():
    df = make_ohlc(MEASURED)
    p = next(p for p in analyze(df)[0] if p.name == "Measured Move Up")
    (_, a), (_, b), (_, c) = p.points
    assert p.confirmed and p.target == pytest.approx(c + (b - a))
    assert df["Close"].iloc[p.end] > b >= df["Close"].iloc[p.end - 1]

    assert "Measured Move Down" in names(make_ohlc([(bars, 250 - x) for bars, x in MEASURED]))
    shallow = make_ohlc([(0, 100.0), (10, 99), (15, 125), (6, 121), (15, 140)])  # a 15% pullback is just a pause
    assert "Measured Move Up" not in names(shallow)


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


def test_piercing_line_and_dark_cloud_cover():
    # opens below the big red candle's close, closes past its middle but not past its open
    pierce = _candles([(61.0, 61.2, 57.0, 57.5), (56.5, 60.0, 56.2, 59.8)])
    sig = candle_signals(pierce, atr(pierce))
    assert sig["piercing"][-1] and not sig["bull_engulfing"][-1]
    shallow = _candles([(61.0, 61.2, 57.0, 57.5), (56.5, 59.0, 56.2, 58.8)])  # stops short of the middle
    assert not candle_signals(shallow, atr(shallow))["piercing"][-1]
    no_gap = _candles([(61.0, 61.2, 57.0, 57.5), (57.5, 60.0, 57.2, 59.8)])  # EGX bars open at the prior close
    assert candle_signals(no_gap, atr(no_gap))["piercing"][-1]

    cloud = mirror(_candles([(61.0, 61.2, 57.0, 57.5), (56.5, 60.0, 56.2, 59.8)]))
    sig = candle_signals(cloud, atr(cloud))
    assert sig["dark_cloud"][-1] and not sig["bear_engulfing"][-1]
    p = [p for p in analyze(cloud)[0] if p.category == "candle"][-1]
    assert (p.name, p.direction, p.start, p.end) == ("Dark Cloud Cover", "bearish", len(cloud) - 2, len(cloud) - 1)


@pytest.mark.parametrize(
    "rows, after_decline, after_rally",
    [
        # every candle opens at the prior close, as EGX bars do
        ([(60.0, 60.1, 55.0, 59.95)], "Dragonfly Doji", "Gravestone Doji"),  # a hair up would make it a harami
        ([(60.0, 60.2, 57.0, 57.5), (57.5, 59.0, 57.05, 58.5)], "Tweezer Bottom", "Tweezer Top"),
        ([(60.0, 61.2, 57.0, 57.5), (57.5, 58.7, 57.45, 58.6)], "Bullish Harami", "Bearish Harami"),
        ([(60.0, 61.2, 57.0, 57.5), (57.5, 58.9, 57.3, 58.7), (58.7, 61.8, 58.5, 61.5)], "Three Inside Up", "Three Inside Down"),
        ([(60.0, 60.2, 58.0, 58.5), (58.5, 61.0, 58.2, 60.5), (60.5, 62.5, 60.3, 62.2)], "Three Outside Up", "Three Outside Down"),
        # three methods continue the trend, so after a decline it's the bearish one
        ([(60.0, 60.2, 55.8, 56.0), (56.0, 57.2, 55.9, 57.0), (57.0, 57.8, 56.6, 57.5), (57.5, 58.3, 57.0, 58.0),
          (58.0, 58.1, 54.5, 54.8)], "Falling Three Methods", "Rising Three Methods"),
    ],
)
def test_more_candlesticks(rows, after_decline, after_rally):
    """Each pattern after a decline, then the same bars upside down after a rally."""
    for df, name in ((_candles(rows), after_decline), (mirror(_candles(rows)), after_rally)):
        p = [p for p in analyze(df)[0] if p.category == "candle"][-1]
        assert (p.name, p.end, p.start) == (name, len(df) - 1, len(df) - len(rows))


def test_three_crows_and_soldiers_open_at_the_prior_close():
    crows = _candles([(60.0, 60.2, 57.0, 57.5), (57.5, 57.7, 54.5, 55.0), (55.0, 55.2, 52.0, 52.5)])
    assert candle_signals(crows, atr(crows))["three_crows"][-1]
    soldiers = mirror(crows)
    assert candle_signals(soldiers, atr(soldiers))["three_soldiers"][-1]


def mirror(df: pd.DataFrame, around: float = 300.0) -> pd.DataFrame:
    """The same bars upside down: rallies become declines, gaps up become gaps down."""
    return pd.DataFrame({"Open": around - df["Open"], "High": around - df["Low"], "Low": around - df["High"],
                         "Close": around - df["Close"], "Volume": df["Volume"]}, index=df.index)


def bars_from(closes: list[float], wick: float = 0.8) -> pd.DataFrame:
    """Bars around each close, each opening at the previous close, so there are no gaps unless a close jumps."""
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) + wick, "Low": np.minimum(o, c) - wick, "Close": c, "Volume": 1e6},
                        index=pd.bdate_range("2024-01-01", periods=len(c)))


def with_gap(df: pd.DataFrame, at: int) -> pd.DataFrame:
    """Open bar `at` at its own close with a short lower wick, so its low clears the previous high."""
    df = df.copy()
    c = df["Close"].iloc[at]
    df.iloc[at, df.columns.get_loc("Open")] = c
    df.iloc[at, df.columns.get_loc("Low")] = c - 0.3
    return df


FLAT = [100.0, 100.6] * 15  # a quiet range: highs ~101.4, lows ~99.2
RISE = list(np.linspace(100, 120, 21))


def gap_patterns(df: pd.DataFrame) -> list:
    return [p for p in analyze(df)[0] if p.category == "gap"]


def test_breakaway_gap_out_of_a_range():
    df = with_gap(bars_from(FLAT + [104.0, 104.5, 105.0, 104.6, 105.5, 106.0, 106.5]), 30)
    (p,) = gap_patterns(df)
    assert (p.name, p.direction, p.confirmed, p.start, p.end) == ("Breakaway Gap", "bullish", True, 29, 35)
    assert p.points[0][1] == pytest.approx(df["High"].iloc[29]) and p.points[1][1] == pytest.approx(df["Low"].iloc[30])

    # back into the range within days: a failed breakout, not a breakaway
    failed = with_gap(bars_from(FLAT + [104.0, 102.0, 100.5, 100.2, 100.4, 100.6, 100.3]), 30)
    assert gap_patterns(failed) == []

    # too recent to know whether it holds: shown as forming, running to the last bar
    fresh = with_gap(bars_from(FLAT + [104.0, 104.5]), 30)
    (p,) = gap_patterns(fresh)
    assert p.name == "Breakaway Gap" and not p.confirmed and p.end == len(fresh) - 1


def test_runaway_gap_holds_and_measures_the_move():
    df = with_gap(bars_from(FLAT + RISE + [124.0, 125.0, 126.0, 127.0, 128.0, 129.0, 130.0]), 51)
    (p,) = gap_patterns(df)
    assert (p.name, p.direction, p.confirmed) == ("Runaway Gap", "bullish", True)
    mid = (df["High"].iloc[50] + df["Low"].iloc[51]) / 2
    start = df["Low"].iloc[31:51].min()
    assert p.target == pytest.approx(mid + (mid - start), rel=1e-3)

    down = gap_patterns(mirror(df))
    assert [(p.name, p.direction) for p in down] == [("Runaway Gap", "bearish")]
    assert 0 < down[0].target < mirror(df)["Close"].iloc[-1]


def test_exhaustion_gap_fills_after_a_run():
    df = with_gap(bars_from(FLAT + RISE + [124.0, 123.0, 121.0, 119.0, 118.0, 117.0, 116.0]), 51)
    (p,) = gap_patterns(df)
    assert (p.name, p.direction, p.confirmed, p.target) == ("Exhaustion Gap", "bearish", True, None)
    fill = next(k for k in range(52, len(df)) if df["Low"].iloc[k] <= df["High"].iloc[50])
    assert p.end == fill


def test_island_reversal_replaces_its_two_gaps():
    closes = FLAT + RISE + [124.0, 124.5, 124.2, 119.0, 118.0, 117.0, 116.0, 115.0]
    df = with_gap(bars_from(closes), 51)
    # gap back down: bar 54 opens and stays below the island's lows
    df.iloc[54, df.columns.get_loc("Open")] = 119.5
    df.iloc[54, df.columns.get_loc("High")] = 120.0
    (p,) = gap_patterns(df)
    assert (p.name, p.direction, p.confirmed, p.start, p.end) == ("Island Top", "bearish", True, 50, 54)
    assert p.points[-1][1] == pytest.approx(df["High"].iloc[51:54].max())  # the island's peak sets the stop

    (b,) = gap_patterns(mirror(df))
    assert (b.name, b.direction) == ("Island Bottom", "bullish")


def test_quiet_series_has_no_gaps():
    assert gap_patterns(make_ohlc(LEAD_UP + [(10, 120), (8, 110), (10, 132)])) == []


def test_pivots_alternate():
    df = make_ohlc(LEAD_UP + [(10, 120), (8, 110), (10, 132), (10, 110)])
    piv = find_pivots(df, order=3)
    kinds = [p.kind for p in piv]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_api_analyze(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))  # no track record runs saved
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
    assert body["patternTrack"] is None
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


def test_outlook_names_the_patterns_it_used():
    from app.outlook import build_outlook

    df = make_ohlc(LEAD_UP + [(12, 120), (10, 105), (12, 120), (12, 95), (6, 92)])
    patterns, levels = analyze(df)
    o = build_outlook(df, patterns, levels, "1d")
    assert patterns[o["mainId"]].name == "Double Top" and o["mainId"] in o["patternIds"]
    used = [patterns[k] for k in o["patternIds"]]
    assert all(p.direction != "neutral" for p in used)
    # old patterns and old candles don't count: only those still in play
    assert all(p.end >= len(df) - 1 - (20 if p.category != "candle" else 2) for p in used)
    assert any(p.end < len(df) - 25 for p in patterns) and len(used) < len(patterns)


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
