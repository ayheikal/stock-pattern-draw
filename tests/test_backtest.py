from __future__ import annotations

import json
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import backtest, main, profile, screener
from app.patterns import Pattern
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
    assert all(isinstance(r["patterns"], list) and isinstance(r["signals"], list) for r in rows)
    assert any(r["signals"] for r in rows)


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


def test_completed_counts_finished_patterns_once():
    P = lambda name, cat, d, end, confirmed=True: Pattern(name=name, category=cat, direction=d, start=0, end=end,
                                                          confidence=0.7, confirmed=confirmed)
    pats = [P("Double Top", "reversal", "bearish", 97), P("Double Top", "reversal", "bearish", 98),
            P("Ascending Triangle", "continuation", "bullish", 99, confirmed=False),  # forming: skipped
            P("Uptrend Line", "trend", "bullish", 99),  # trend: skipped
            P("Runaway Gap", "gap", "bullish", 90)]  # finished before the last 5 bars
    assert backtest.completed(pats, 100, 5) == [["Double Top", "bearish"]]


def test_summarize_patterns():
    s = lambda ret, *pats: {"bias": "Neutral", "fwdRet": ret, "outcome": None, "R": None, "patterns": [list(p) for p in pats]}
    dt, ham, doji = ("Double Top", "bearish"), ("Bull Flag", "bullish"), ("Rectangle", "neutral")
    samples = [s(-2.0, dt), s(-1.0, dt, ham), s(3.0, dt), s(1.0, ham, doji), s(2.0)]
    out = {p["name"]: p for p in backtest.summarize_patterns(samples)}
    assert [p["name"] for p in backtest.summarize_patterns(samples)] == ["Double Top", "Bull Flag", "Rectangle"]
    assert out["Double Top"]["n"] == 3 and out["Double Top"]["avgRet"] == 0.0
    assert out["Double Top"]["asExpected"] == pytest.approx(66.7) and out["Double Top"]["baseRate"] == 40.0  # 3 of 5 rose
    assert out["Bull Flag"]["asExpected"] == 50.0 and out["Bull Flag"]["baseRate"] == 60.0
    assert out["Bull Flag"]["excess"] == pytest.approx(0.0 - 0.6) and out["Bull Flag"]["small"]
    assert out["Rectangle"]["asExpected"] is None and out["Rectangle"]["baseRate"] is None
    assert backtest.summarize_patterns([{"fwdRet": 1.0}]) == []  # samples saved before patterns were tracked


def test_pattern_trades_aim_at_the_target_with_a_stop_past_the_last_swing():
    w = bars([(101, 99, 100)] * 30 + [(104, 98, 103), (106, 102, 105)])  # last close 105, low since bar 30 is 98
    P = lambda **k: Pattern(**({"name": "Bull Flag", "category": "continuation", "direction": "bullish", "start": 20,
                                "end": 31, "confidence": 0.7, "points": [(25, 99.0), (30, 98.0)], "target": 120.0,
                                "confirmed": True} | k))
    (t,) = backtest.pattern_trades([P()], w, 5)
    a = float(backtest.compute_atr(w)[-1])
    assert (t["side"], t["entry"], t["target"]) == ("long", 105.0, 120.0)
    assert t["stop"] == pytest.approx(98 - 0.25 * a) and t["rr"] == pytest.approx(15 / (105 - t["stop"]))
    # no trade: forming, no target, target already passed, or too old
    for skip in (P(confirmed=False), P(target=None), P(target=104.0), P(end=20)):
        assert backtest.pattern_trades([skip], w, 5) == []
    (s,) = backtest.pattern_trades([P(name="Double Top", direction="bearish", target=90.0, category="reversal")], w, 5)
    assert s["side"] == "short" and s["stop"] == pytest.approx(106 + 0.25 * a)


def test_success_rate_counts_target_before_stop():
    t = lambda outcome, R: {"name": "Double Top", "direction": "bearish", "outcome": outcome, "R": R}
    samples = [{"fwdRet": -1.0, "patterns": [["Double Top", "bearish"]], "patternTrades": [t("target", 2.0)]},
               {"fwdRet": 1.0, "patterns": [["Double Top", "bearish"]], "patternTrades": [t("stop", -1.0)]},
               {"fwdRet": -2.0, "patterns": [["Double Top", "bearish"]], "patternTrades": [t("target", 1.5)]},
               {"fwdRet": 0.5, "patterns": [["Double Top", "bearish"]], "patternTrades": [t("timeout", 0.2)]}]
    (p,) = backtest.summarize_patterns(samples)
    assert (p["trades"], p["targetFirst"], p["stopFirst"]) == (4, 50.0, 25.0)
    assert p["avgR"] == pytest.approx((2 - 1 + 1.5 + 0.2) / 4, abs=1e-3)
    (s,) = backtest.summarize_patterns([{"fwdRet": 1.0, "signals": [["RSI back above 30", "bullish"]]}], "signals")
    assert s["trades"] == 0 and s["targetFirst"] is None


def test_pattern_verdict():
    v = lambda n, said, base=50.0: backtest.pattern_verdict({"n": n, "asExpected": said, "baseRate": base})
    assert v(400, 60.0) == "useful" and v(400, 40.0) == "misleading"
    assert v(40, 60.0) == "unclear"  # 10 points, but within luck for 40 signals
    assert v(400, 52.0) == "none" and v(29, 90.0) == "few"
    assert backtest.pattern_verdict({"n": 500, "asExpected": None, "baseRate": None}) is None


def test_pattern_track_uses_the_largest_run_of_the_same_market(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    pat = lambda name, n: {"name": name, "direction": "bearish", "n": n, "asExpected": 40.0, "baseRate": 50.0}
    run = lambda uni, syms, n, pats: {"universe": uni, "symbols": len(syms), "since": "2016-01-01", "horizon": "next week",
                                      "overall": {"n": n}, "perSymbol": dict.fromkeys(syms, {}), "patterns": pats}
    files = {"egx": run("egx", ["EGX:COMI", "EGX:ETEL", "EGX:EGX30"], 900, [pat("Shooting Star", 400)]),
             "egx_banks": run("egx_banks", ["EGX:COMI"], 100, [pat("Shooting Star", 20)]),
             "world": run("world", ["^GSPC", "^NDX"], 500, [pat("Double Top", 60)]),
             "old": run("old", ["EGX:COMI"], 5000, None)}  # saved before patterns were tracked
    for k, r in files.items():
        (tmp_path / f"backtest_{k}_1d.json").write_text(json.dumps(r), encoding="utf-8")

    t = backtest.pattern_track("COMI.CA", "1d")
    assert (t["universe"], t["label"], t["symbols"]) == ("egx", "EGX", 3)
    assert t["stats"]["Shooting Star|bearish"] == {"n": 400, "asExpected": 40.0, "baseRate": 50.0, "verdict": "misleading",
                                                   "trades": 0, "targetFirst": None, "stopFirst": None, "avgR": None}
    assert backtest.pattern_track("AAPL", "1d")["stats"].keys() == {"Double Top|bearish"}
    assert backtest.pattern_track("COMI.CA", "1wk") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(backtest, "PARALLEL", False)
    df = make_ohlc(UPTREND)
    monkeypatch.setattr(backtest, "fetch_ohlcv", lambda s, i, p: (f"{s}.X", df, {"longName": s}))
    monkeypatch.setitem(screener.UNIVERSES, "tiny", {"label": "Tiny", "symbols": ["AAA", "BBB"]})
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
    assert isinstance(res["patterns"], list) and all(0 < p["n"] <= res["overall"]["n"] for p in res["patterns"])
    assert all("patterns" in part and "signals" in part for part in backtest.load("tiny", "1d")["ranges"].values())
    assert res["signals"] and all(0 < s["n"] <= res["overall"]["n"] for s in res["signals"])

    track = backtest.track_for("AAA.X", "1d")
    live_bias = next(b["bias"] for b in track["buckets"] if b["n"])
    row = {"symbol": "AAA.X", "name": "A", "currency": "EGP", "last": 100.0, "changePct": 0.0, "bias": live_bias,
           "score": 0.0, "upTarget": 110.0, "downTarget": 90.0, "action": "Wait", "signal": ""}
    out = profile.compare(profile.Entry(symbol="AAA"), row, track)
    assert out["track"]["bias"] == live_bias and out["track"]["n"] > 0
    # the last year's record comes along, so a fading edge shows next to the all-history one
    assert track["recent"]["range"] == "1y" and "buckets" in track["recent"]
    assert out["track"]["recent"] is None or out["track"]["recent"]["range"] == "1y"


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


def test_net_of_costs():
    s = lambda bias, ret, outcome=None, R=None, risk=None: {"bias": bias, "fwdRet": ret, "outcome": outcome, "R": R, "riskPct": risk}
    # a +2R winner risking 2% of the price: 0.2% fee each way costs 0.4/2 = 0.2R
    out = backtest.summarize_samples([s("Bullish", 3.0, "target", 2.0, 2.0), s("Bullish", -1.0, "stop", -1.0, 4.0)], fee_pct=0.2)
    b = next(x for x in out["buckets"] if x["bias"] == "Bullish")
    assert b["avgR"] == 0.5 and b["avgRNet"] == pytest.approx((1.8 + -1.1) / 2, abs=1e-3)
    assert b["avgRetNet"] == pytest.approx(1.0 - 0.4)
    neutral = next(x for x in out["buckets"] if x["bias"] == "Neutral")
    assert neutral["avgRetNet"] is None


def test_liquidity_filter_skips_thin_bars():
    df = make_ohlc(UPTREND)
    df["Volume"] = 1000.0  # ~50-130 x 1000 = well under 1M a day
    assert backtest.replay(df, "1d", min_adtv=1_000_000) == []
    df.loc[df.index[280:], "Volume"] = 1e6  # liquid from bar 280 on (~100M a day lifts the 20-bar average at once)
    rows = backtest.replay(df, "1d", min_adtv=1_000_000)
    all_rows = backtest.replay(df, "1d")
    assert 0 < len(rows) < len(all_rows)
    assert all(r["date"] >= df.index[280].strftime("%Y-%m-%d") for r in rows)
