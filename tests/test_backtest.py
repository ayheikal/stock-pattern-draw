from __future__ import annotations

import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import backtest, main, profile
from test_patterns import make_ohlc

UPTREND = [(0, 50.0), (80, 70), (20, 64), (80, 90), (20, 84), (80, 110), (20, 100), (80, 130)]


def bars(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """(high, low, close) rows -> OHLCV frame."""
    idx = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame({"Open": [c for _, _, c in rows], "High": [h for h, _, _ in rows],
                         "Low": [l for _, l, _ in rows], "Close": [c for _, _, c in rows], "Volume": 1e6}, index=idx)


LONG = {"side": "long", "entry": 100.0, "target": 110.0, "stop": 95.0, "rr": 2.0}


def test_plan_outcome_target_first():
    df = bars([(100, 100, 100), (104, 99, 103), (111, 102, 109), (112, 90, 91)])
    assert backtest.plan_outcome(df, 0, LONG, 10) == ("target", 2.0)


def test_plan_outcome_stop_first_and_same_bar_counts_as_stop():
    assert backtest.plan_outcome(bars([(100, 100, 100), (101, 94, 95), (120, 99, 119)]), 0, LONG, 10) == ("stop", -1.0)
    assert backtest.plan_outcome(bars([(100, 100, 100), (112, 94, 100)]), 0, LONG, 10) == ("stop", -1.0)


def test_plan_outcome_timeout_marks_to_market_and_handles_shorts():
    assert backtest.plan_outcome(bars([(100, 100, 100), (103, 99, 102), (104, 101, 102.5)]), 0, LONG, 2) == ("timeout", 0.5)
    short = {"side": "short", "entry": 100.0, "target": 90.0, "stop": 105.0, "rr": 2.0}
    assert backtest.plan_outcome(bars([(100, 100, 100), (101, 89, 90)]), 0, short, 5) == ("target", 2.0)


def test_no_look_ahead():
    df = make_ohlc(UPTREND)
    i = 300
    assert backtest.sample_at(df, i, "1d") == backtest.sample_at(df.iloc[: i + 1], i, "1d")
    # changing the future must not change the signal
    future = df.copy()
    future.iloc[i + 1:, :4] *= 0.5
    assert backtest.sample_at(future, i, "1d") == backtest.sample_at(df, i, "1d")


def test_replay_samples_are_spaced_and_complete():
    df = make_ohlc(UPTREND)
    rows = backtest.replay(df, "1d")
    h = backtest.horizon_bars("1d")
    assert len(rows) == len(range(backtest.WINDOW - 1, len(df) - 4 * h, h))
    assert all(r["bias"] in backtest.BUCKETS for r in rows)
    assert any(r["outcome"] for r in rows)


def test_summarize_samples():
    s = lambda bias, ret, outcome=None, R=None: {"bias": bias, "fwdRet": ret, "outcome": outcome, "R": R}
    samples = [s("Bullish", 4.0, "target", 2.0), s("Bullish", -2.0, "stop", -1.0), s("Bullish", 1.0, "timeout", 0.3),
               s("Neutral", 0.0), s("Bearish", -3.0, "target", 1.5), s("Bearish", 2.0, "stop", -1.0)]
    out = backtest.summarize_samples(samples)
    assert out["n"] == 6 and out["baseline"] == pytest.approx(0.333, abs=1e-3)
    b = {x["bias"]: x for x in out["buckets"]}
    assert b["Bullish"]["n"] == 3 and b["Bullish"]["avgRet"] == 1.0
    assert b["Bullish"]["asExpected"] == pytest.approx(66.7)
    assert (b["Bullish"]["targetFirst"], b["Bullish"]["stopFirst"], b["Bullish"]["timeout"]) == (33.3, 33.3, 33.3)
    assert b["Bullish"]["avgR"] == pytest.approx(0.433, abs=1e-3) and b["Bullish"]["small"]
    assert b["Bearish"]["asExpected"] == 50.0 and b["Neutral"]["asExpected"] is None
    assert b["Mildly bullish"]["n"] == 0 and b["Mildly bullish"]["avgRet"] is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(backtest, "PARALLEL", False)
    df = make_ohlc(UPTREND)
    monkeypatch.setattr(backtest, "fetch_ohlcv", lambda s, i, p: (f"{s}.X", df, {"longName": s}))
    monkeypatch.setitem(backtest.UNIVERSES, "tiny", {"label": "Tiny", "symbols": ["AAA", "BBB"]})
    return TestClient(main.app)


def test_backtest_api_run_and_profile_track(client, monkeypatch):
    body = client.get("/api/backtest?universe=tiny").json()
    assert body["result"] is None and not body["status"]["running"]
    assert client.post("/api/backtest/run?universe=tiny").json() == {"started": True}
    for _ in range(200):
        body = client.get("/api/backtest?universe=tiny").json()
        if not body["status"]["running"] and body["result"]:
            break
        time.sleep(0.05)
    res = body["result"]
    assert res["symbols"] == 2 and set(res["perSymbol"]) == {"AAA.X", "BBB.X"}
    assert sum(b["n"] for b in res["overall"]["buckets"]) == res["overall"]["n"]

    track = backtest.track_for("AAA.X", "1d")
    live_bias = next(b["bias"] for b in track["buckets"] if b["n"])
    row = {"symbol": "AAA.X", "name": "A", "currency": "EGP", "last": 100.0, "changePct": 0.0, "bias": live_bias,
           "score": 0.0, "upTarget": 110.0, "downTarget": 90.0, "action": "Wait", "signal": ""}
    out = profile.compare(profile.Entry(symbol="AAA"), row, track)
    assert out["track"]["bias"] == live_bias and out["track"]["n"] > 0


def test_backtest_api_rejects_bad_args(client):
    assert client.get("/api/backtest?universe=nope").status_code == 400
    assert client.post("/api/backtest/run?interval=1h").status_code == 400


def test_periods_slice_signals_by_date(client):
    client.post("/api/backtest/run?universe=tiny")
    for _ in range(200):
        body = client.get("/api/backtest?universe=tiny").json()
        if body["result"] and not body["status"]["running"]:
            break
        time.sleep(0.05)
    full = body["result"]
    assert full["periods"] == ["all", "6mo", "1y", "2y", "5y"]
    counts = {}
    for rng in full["periods"]:
        r = client.get(f"/api/backtest?universe=tiny&range={rng}").json()["result"]
        assert r["range"] == rng
        counts[rng] = r["overall"]["n"]
        # per-symbol counts add up to the overall count for every period
        assert sum(s["n"] for s in r["perSymbol"].values()) == r["overall"]["n"]
    assert 0 < counts["6mo"] < counts["1y"] <= counts["all"]  # signals start after the 250-bar warm-up
    assert counts["2y"] == counts["5y"] == counts["all"]  # the synthetic history is under 2 years long
    assert client.get("/api/backtest?universe=tiny&range=3y").status_code == 400


def test_cutoff_and_old_cache_without_periods():
    assert backtest.cutoff("2026-09-25", 6) == "2026-03-25"
    old = {"universe": "egx", "interval": "1d", "range": "all", "overall": {}, "perSymbol": {}}
    assert backtest.view(old, "1y")["rangeMissing"] is True
    assert "rangeMissing" not in backtest.view(old, "all")
