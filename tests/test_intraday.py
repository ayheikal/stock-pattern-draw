"""Intraday setup: session VWAP, relative volume, the five rules and the endpoint (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import indicators, intraday, intraday_backtest, intraday_log, main

PER_DAY = 16  # 15-minute bars from 10:00 to 13:45


def frame(closes, volume=1e5, opens=None) -> pd.DataFrame:
    """15-minute bars, PER_DAY a session, on consecutive business days."""
    c = np.asarray(closes, dtype=float)
    days = pd.bdate_range("2024-03-04", periods=-(-len(c) // PER_DAY), tz="Africa/Cairo")
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=10, minutes=15 * i) for d in days for i in range(PER_DAY)][: len(c)])
    o = c if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) + 0.05, "Low": np.minimum(o, c) - 0.05, "Close": c,
                         "Volume": np.broadcast_to(volume, c.shape).astype(float)}, index=idx)


LATER = pd.Timestamp("2030-01-01", tz="Africa/Cairo")  # every bar is complete by then


@pytest.fixture
def client():
    return TestClient(main.app)


def test_vwap_restarts_each_session():
    df = frame(np.r_[np.full(PER_DAY, 10.0), np.linspace(20, 23, PER_DAY)], volume=np.r_[np.full(PER_DAY, 100.0), np.arange(1, PER_DAY + 1) * 50.0])
    v = indicators.session_vwap(df)
    assert v[PER_DAY - 1] == pytest.approx(10.0)
    assert v[PER_DAY] == pytest.approx(20.0)                      # the first bar of day 2 owes nothing to day 1
    d2 = df.iloc[PER_DAY:]
    tp = (d2["High"] + d2["Low"] + d2["Close"]) / 3
    assert v[-1] == pytest.approx((tp * d2["Volume"]).sum() / d2["Volume"].sum())
    assert np.isnan(indicators.session_vwap(frame(np.full(PER_DAY, 10.0), volume=0.0))).all()


def test_relative_volume_compares_the_same_time_of_day():
    vol = np.r_[np.full(PER_DAY * 3, 100.0), np.full(8, 200.0)]   # day 4, half way through, at double the pace
    df = frame(np.full(len(vol), 10.0), volume=vol)
    assert indicators.relative_volume(df) == pytest.approx(2.0)
    assert indicators.relative_volume(df.iloc[:PER_DAY]) is None  # nothing earlier to compare with
    assert indicators.relative_volume(frame(np.full(40, 10.0), volume=0.0)) is None


def test_adx_tells_a_trend_from_a_range():
    trend = frame(np.linspace(100, 160, 120))
    chop = frame(100 + np.tile([0.3, -0.3], 60))
    assert indicators.adx(trend)[-1] > 25 > indicators.adx(chop)[-1]


def test_breakout_on_heavy_volume_passes_all_five():
    c = np.r_[np.full(PER_DAY * 3, 50.0), np.linspace(50.2, 53, 10)]
    vol = np.r_[np.full(PER_DAY * 3, 100.0), np.full(10, 300.0)]
    e = intraday.evaluate(frame(c, volume=vol), "15m", now=LATER)
    assert e["passed"] == 5 and e["entry"] == "breakout" and all(k["ok"] for k in e["checks"])
    assert e["rvol"] == pytest.approx(3.0) and e["vwapPct"] > 0 and e["rsi"] > 50
    assert e["stop"] == pytest.approx(e["last"] - 1.5 * e["atr"], abs=1e-3)
    assert e["target"] == pytest.approx(e["last"] + 3 * e["atr"], abs=1e-3)
    assert e["dayPct"] == pytest.approx(6.0) and e["lastBar"].endswith("12:15")


def test_downtrend_fails_trend_vwap_and_rsi():
    e = intraday.evaluate(frame(np.linspace(60, 50, PER_DAY * 3 + 10)), "15m", now=LATER)
    ok = {k["key"]: k["ok"] for k in e["checks"]}
    assert ok == {"trend": False, "vwap": False, "rsi": False, "volume": False, "entry": False}
    assert e["passed"] == 0 and e["entry"] is None


def test_retest_of_ema20_counts_as_an_entry():
    # a steady climb, a two-bar dip back to the average, then an up bar that closes back above it (below the old high)
    c = np.r_[np.linspace(50, 56, PER_DAY * 3), 55.2, 54.9, 55.5]
    o = np.r_[c[0], c[:-1]]
    e = intraday.evaluate(frame(c, opens=o), "15m", now=LATER)
    assert e["entry"] == "retest" and e["last"] < 56


def test_no_volume_reads_no_data_not_fail():
    e = intraday.evaluate(frame(np.linspace(50, 56, 60), volume=0.0), "15m", now=LATER)
    ok = {k["key"]: k["ok"] for k in e["checks"]}
    assert ok["vwap"] is None and ok["volume"] is None and ok["trend"] and e["vwap"] is None and e["passed"] < 5


def test_a_forming_bar_is_left_out():
    df = frame(np.linspace(50, 56, 60))
    during = df.index[-1] + pd.Timedelta(minutes=5)
    assert intraday.evaluate(df, "15m", now=during)["lastBar"] == df.index[-2].strftime("%Y-%m-%d %H:%M")
    assert intraday.evaluate(df, "15m", now=LATER)["lastBar"] == df.index[-1].strftime("%Y-%m-%d %H:%M")


def test_scan_ranks_by_rules_passed_and_skips_bad_symbols(monkeypatch):
    good = frame(np.r_[np.full(PER_DAY * 3, 50.0), np.linspace(50.2, 53, 10)], volume=np.r_[np.full(PER_DAY * 3, 100.0), np.full(10, 300.0)])
    bad = frame(np.linspace(60, 50, PER_DAY * 3 + 10))

    def fetch(symbol, interval, period):
        assert (interval, period) == ("15m", "1mo")
        if symbol == "GONE":
            raise intraday.SymbolNotFound(symbol)
        return symbol, {"UP": good, "DOWN": bad}[symbol], {"longName": symbol.title(), "currency": "EGP"}

    monkeypatch.setattr(intraday, "fetch_ohlcv", fetch)
    monkeypatch.setattr(intraday, "_completed", lambda df, interval, now: df)
    out = intraday.scan(["DOWN", "GONE", "UP"], "15m")
    assert [r["symbol"] for r in out["results"]] == ["UP", "DOWN"] and out["skipped"] == ["GONE"] and out["scanned"] == 2


def test_endpoint_filters_by_show_and_validates(client, monkeypatch):
    rows = lambda: [{"symbol": s, "currency": "EGP", "passed": p, "rvol": 1.0} for s, p in (("A.CA", 5), ("B.CA", 4), ("C.CA", 2))]
    monkeypatch.setattr(intraday, "scan", lambda syms, interval: {"scanned": 3, "skipped": [], "results": rows()})
    get = lambda q: client.get(f"/api/intraday?symbols=A.CA,B.CA,C.CA&{q}")
    b = get("interval=15m").json()
    assert [r["symbol"] for r in b["results"]] == ["A.CA"] and b["hidden"] == 2 and "halal" in b["results"][0]
    assert [r["symbol"] for r in get("show=4plus").json()["results"]] == ["A.CA", "B.CA"]
    assert len(get("show=all&interval=5m").json()["results"]) == 3
    assert get("halal_only=true&show=all").json()["results"] == []          # nothing is tagged halal in an empty profile
    assert get("interval=1d").status_code == 400 and get("show=nope").status_code == 400
    assert client.get("/api/intraday?universe=nope").status_code == 400
    assert client.get("/intraday").status_code == 200
    assert client.get("/api/screen?symbols=A.CA&interval=5m").status_code == 400   # other pages still refuse 5m bars


# ---- the trade a signal stands for, the backtest and the live record ----

def bars(o, h, l, c, day, atr=1.0) -> dict:
    """Just what intraday.trade reads; with ATR 1 a 100 entry has its stop at 98.5 and target at 103."""
    return {"open": np.array(o, float), "high": np.array(h, float), "low": np.array(l, float), "close": np.array(c, float),
            "day": np.array(day), "atr": np.full(len(o), atr)}


def test_trade_buys_the_next_open_and_exits_at_target_stop_or_the_close():
    t = intraday.trade(bars([100, 100, 101, 102], [100.5, 101, 102, 103.5], [99.5, 99.5, 100.5, 101.5], [100, 100.5, 101.5, 103], [1] * 4), 0)
    assert (t["outcome"], t["entry"], t["exit"], t["exitIdx"]) == ("target", 100, 103, 3) and t["R"] == pytest.approx(2.0)
    t = intraday.trade(bars([100, 100, 100], [100.5, 101, 100.5], [99.5, 99.5, 98], [100, 100.5, 98.2], [1] * 3), 0)
    assert (t["outcome"], t["exit"]) == ("stop", 98.5) and t["R"] == pytest.approx(-1.0)
    both = bars([100, 100, 100], [100.5, 101, 104], [99.5, 99.5, 98], [100, 100.5, 103], [1] * 3)
    assert intraday.trade(both, 0)["outcome"] == "stop"                      # a bar touching both is counted against us
    gap = bars([100, 100, 97], [100.5, 101, 97.5], [99.5, 99.5, 96], [100, 100.5, 97], [1] * 3)
    assert intraday.trade(gap, 0)["exit"] == 97                              # gapped through the stop: filled at the open
    quiet = bars([100, 100, 100.2, 105], [100.5, 100.6, 100.8, 106], [99.5, 99.6, 99.9, 104], [100, 100.2, 100.6, 105], [1, 1, 1, 2])
    t = intraday.trade(quiet, 0)
    assert (t["outcome"], t["exit"], t["exitIdx"]) == ("time", 100.6, 2)      # out at the session close, not carried overnight
    assert intraday.trade(quiet, 2)["outcome"] == "skipped"                  # signal on the last bar of the session
    assert intraday.trade(quiet, 3)["outcome"] == "pending"                  # the next bar has not printed yet
    same_day = bars([100, 100, 100.2], [100.5, 100.6, 100.8], [99.5, 99.6, 99.9], [100, 100.2, 100.6], [1] * 3)
    assert intraday.trade(same_day, 0, day_over=False)["outcome"] == "open" and intraday.trade(same_day, 0)["outcome"] == "time"


def rising_day() -> pd.DataFrame:
    """Three flat sessions, a session that climbs all day on heavy volume, then a flat one."""
    c = np.r_[np.full(PER_DAY * 3, 50.0), np.linspace(50.2, 56, PER_DAY), np.full(PER_DAY, 56.0)]
    return frame(c, volume=np.r_[np.full(PER_DAY * 3, 100.0), np.full(PER_DAY * 2, 300.0)])


def test_signals_at_a_bar_never_use_later_bars():
    c = np.r_[np.full(PER_DAY * 3, 50.0), np.linspace(50.2, 53, 10), np.linspace(53, 49, 6)]
    df = frame(c, volume=np.r_[np.full(PER_DAY * 3, 100.0), np.full(16, 300.0)])
    full, cut = intraday.signals(df), intraday.signals(df.iloc[:55])
    assert (full["passed"][:55] == cut["passed"]).all() and full["passed"][54] == 5
    assert intraday.evaluate(df.iloc[:55], "15m", now=LATER)["passed"] == 5   # the live scan reads the same rules


def test_replay_takes_one_trade_at_a_time_and_drops_unfinished_ones():
    tr = intraday_backtest.replay(frame(np.full(PER_DAY * 4, 50.0)))
    # nothing passes; buying every bar blindly gives one trade per finished session after the warm-up (the last day is still open)
    assert tr["all5"] == [] and [t["outcome"] for t in tr["any"]] == ["time", "time"]
    tr = intraday_backtest.replay(rising_day())
    # the climbing session: a new trade only after the one before is out, each one a winner
    assert [(t["at"][-5:], t["outcome"]) for t in tr["all5"]] == [("10:15", "target"), ("11:00", "target"), ("12:00", "target"), ("13:00", "time")]
    assert all(t["R"] > 0 for t in tr["all5"])
    assert all(len(tr[f"no_{k}"]) >= len(tr["all5"]) for k in intraday.RULES)  # dropping a rule can only let more through


def test_summary_takes_costs_off_and_needs_enough_trades():
    win = {"at": "2024-03-04 10:00", "outcome": "target", "R": 2.0, "retPct": 1.0, "riskPct": 0.5}
    lose = {"at": "2024-03-05 10:00", "outcome": "stop", "R": -1.0, "retPct": -0.5, "riskPct": 0.5}
    s = intraday_backtest.summarize([win, lose], 0.6)
    assert (s["n"], s["win"], s["target"], s["stop"], s["avgR"], s["avgRet"]) == (2, 50.0, 50.0, 50.0, 0.5, 0.25)
    assert s["avgNet"] == pytest.approx(-0.35) and s["profitFactor"] == pytest.approx(0.36, abs=0.01) and s["verdict"] == "few"
    assert (s["firstHalf"], s["secondHalf"]) == (pytest.approx(0.4), pytest.approx(-1.1))
    many = [win | {"retPct": 1.0 + i * 0.01} for i in range(40)]
    assert intraday_backtest.summarize(many, 0.6)["verdict"] == "profit" and intraday_backtest.summarize(many, 3.0)["verdict"] == "loss"
    assert intraday_backtest.summarize([win, lose] * 20, 0.25)["verdict"] == "unclear"
    assert intraday_backtest.summarize([], 0.6)["n"] == 0


def test_backtest_run_skips_indices_and_reports_each_rule_set(monkeypatch):
    c = np.r_[np.full(PER_DAY * 6, 50.0), np.linspace(50.2, 56, PER_DAY), np.full(PER_DAY, 56.0)]
    stock = frame(c, volume=np.r_[np.full(PER_DAY * 6, 100.0), np.full(PER_DAY * 2, 300.0)])
    data = {"UP.CA": (stock, {"currency": "EGP"}), "EGX30": (frame(c, volume=0.0), {})}
    monkeypatch.setattr(intraday_backtest, "fetch_ohlcv", lambda s, i, p: (s, *data[s]))
    r = intraday_backtest.run("x", "15m", symbols=["UP.CA", "EGX30"], fee_pct=0.2)
    assert r["symbols"] == 1 and r["skipped"] == ["EGX30 (an index: no volume)"] and r["costPct"] == pytest.approx(0.6)
    by = {s["key"]: s for s in r["strategies"]}
    assert list(by) == ["all5", "4plus", "no_trend", "no_vwap", "no_rsi", "no_volume", "no_entry", "any"]
    assert by["all5"]["n"] == 4 and by["all5"]["win"] == 100.0 and by["any"]["n"] > 4 and r["perSymbol"][0]["symbol"] == "UP.CA"
    intraday_backtest.save(r)
    assert intraday_backtest.load("x", "15m")["bars"] == r["bars"] and intraday_backtest.load("x", "5m") is None


def row(df: pd.DataFrame, i: int, symbol: str = "UP.CA", passed: int = 5) -> dict:
    return {"symbol": symbol, "name": symbol, "passed": passed, "entry": "breakout", "last": float(df["Close"].iloc[i]),
            "barTime": df.index[i].isoformat(), "lastBar": df.index[i].strftime("%Y-%m-%d %H:%M")}


def test_live_record_logs_fresh_setups_once_and_scores_them():
    df = rising_day()
    i = PER_DAY * 3 + 5
    soon = df.index[i] + pd.Timedelta(minutes=20)                               # the bar closed 5 minutes ago
    assert intraday_log.record([row(df, i), row(df, i, "FOUR.CA", passed=4)], "15m", "scan", now=soon) == 1
    assert intraday_log.record([row(df, i), row(df, i + 1)], "15m", "scan", now=soon) == 0    # logged already, and still open
    assert intraday_log.record([row(df, i, "LATE.CA")], "15m", "scan", now=soon + pd.Timedelta(hours=2)) == 0   # an old bar
    last_of_day = PER_DAY * 3 - 1
    assert intraday_log.record([row(df, last_of_day, "END.CA")], "15m", "auto", now=df.index[last_of_day] + pd.Timedelta(minutes=20)) == 1

    so_far = lambda s, interval, period: (s, df.iloc[: i + 2], {})               # one bar after the signal has printed
    assert intraday_log.score(now=df.index[i + 1] + pd.Timedelta(minutes=16), fetch=so_far) == 1   # END.CA: no next bar that session
    by = {s["symbol"]: s for s in intraday_log.load()["signals"]}
    assert by["END.CA"]["status"] == "skipped" and by["UP.CA"]["status"] == "open" and by["UP.CA"]["entry"] == pytest.approx(df["Open"].iloc[i + 1])
    assert intraday_log.score(now=LATER, fetch=lambda s, interval, period: (s, df, {})) == 1
    up = next(s for s in intraday_log.load()["signals"] if s["symbol"] == "UP.CA")
    assert up["status"] in intraday_log.DONE and up["entry"] == pytest.approx(df["Open"].iloc[i + 1]) and up["R"] > 0

    rep = intraday_log.report(fee_pct=0.2)
    assert (rep["total"], rep["open"], rep["costPct"]) == (2, 0, 0.6)
    assert [(s["key"], s["n"]) for s in rep["summary"]] == [("all", 1), ("15m", 1)]
    assert rep["summary"][0]["avgNet"] == pytest.approx(up["retPct"] - 0.6, abs=1e-3)
    assert rep["signals"][0]["symbol"] == "UP.CA"                                # newest first


def test_backtest_and_log_endpoints(client, monkeypatch):
    started = []
    monkeypatch.setattr(intraday_backtest, "start", lambda u, i, fee: started.append((u, i, fee)) or True)
    assert client.get("/api/intraday/backtest?universe=egx&interval=15m").json() == {"status": {"running": False, "error": None}, "result": None}
    assert client.post("/api/intraday/backtest/run?universe=egx&interval=5m").json() == {"started": True} and started[0][:2] == ("egx", "5m")
    assert client.get("/api/intraday/backtest?interval=1d").status_code == 400
    assert client.post("/api/intraday/backtest/run?universe=nope").status_code == 400

    assert client.get("/api/intraday/log").json()["auto"]["on"] is False
    assert client.put("/api/intraday/log/auto", json={"on": True, "universe": "us", "interval": "15m"}).status_code == 400   # EGX hours only
    assert client.put("/api/intraday/log/auto", json={"on": True, "universe": "egx", "interval": "5m"}).json() == {"on": True, "universe": "egx", "interval": "5m"}
    log = client.post("/api/intraday/log/score").json()
    assert log["auto"]["on"] is True and log["total"] == 0 and log["summary"][0]["n"] == 0


def test_a_scan_puts_fresh_setups_on_the_record(client, monkeypatch):
    df = rising_day()
    recent = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=20)).isoformat()
    fresh = row(df, PER_DAY * 3 + 5) | {"currency": "EGP", "rvol": 3.0, "barTime": recent}
    monkeypatch.setattr(intraday, "scan", lambda syms, interval: {"scanned": 1, "skipped": [], "results": [dict(fresh)]})
    assert client.get("/api/intraday?symbols=UP.CA").json()["logged"] == 1
    assert client.get("/api/intraday?symbols=UP.CA").json()["logged"] == 0
    assert client.get("/api/intraday/log").json()["open"] == 1
