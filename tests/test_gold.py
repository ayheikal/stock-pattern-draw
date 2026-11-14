"""Gold in pounds: the conversion, the split of a move into gold and the pound, and the endpoint (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import gold, main
from app.data import SymbolNotFound


def series(values, start="2025-01-01", tz="UTC", hour=0) -> pd.Series:
    idx = (pd.bdate_range(start, periods=len(values)) + pd.Timedelta(hours=hour)).tz_localize(tz)
    return pd.Series(np.asarray(values, dtype=float), index=idx)


@pytest.fixture
def client():
    return TestClient(main.app)


def test_prices_follow_the_ounce_the_rate_and_the_karat():
    p = gold.prices(3110.34768, 50.0)                       # exactly 100 dollars a gram
    assert p["gram"] == {"24": 5000.0, "21": 4375.0, "18": 3750.0}
    assert p["pound"] == 35000.0 and p["ounceEgp"] == pytest.approx(155517.38) and p["rate"] == 50.0


def test_a_bar_is_dated_by_the_day_it_trades_not_the_evening_it_opens():
    # spot gold's Monday bar opens Sunday 18:00 in New York; the dollar's bar is stamped at midnight; EGX at 10:00 in Cairo
    spot = pd.DatetimeIndex([pd.Timestamp("2026-11-08 18:00", tz="America/New_York")])
    assert gold.trade_dates(spot)[0] == pd.Timestamp("2026-11-09")
    assert gold.trade_dates(pd.DatetimeIndex([pd.Timestamp("2026-11-09 00:00", tz="Europe/London")]))[0] == pd.Timestamp("2026-11-09")
    assert gold.trade_dates(pd.DatetimeIndex([pd.Timestamp("2026-11-09 10:00", tz="Africa/Cairo")]))[0] == pd.Timestamp("2026-11-09")
    assert gold.trade_dates(pd.DatetimeIndex([pd.Timestamp("2026-11-09")]))[0] == pd.Timestamp("2026-11-09")   # no timezone: as given


def test_combine_carries_a_closed_market_over_and_starts_where_both_have_prices():
    g = series([2000, 2010, 2020, 2030], start="2025-01-06")            # Mon to Thu
    r = series([50.0, 51.0], start="2025-01-07")                        # Tue, Wed only
    df = gold.combine(g, r)
    assert list(df.index.strftime("%m-%d")) == ["01-07", "01-08", "01-09"]
    assert df["rate"].tolist() == [50.0, 51.0, 51.0] and df["gram24"].iloc[-1] == pytest.approx(2030 * 51 / gold.OUNCE_GRAMS)


def test_changes_split_the_pound_move_into_gold_and_the_dollar():
    n = 300
    g = series(np.r_[np.full(n - 1, 2000.0), 2200.0])                   # gold up 10% on the last day
    r = series(np.r_[np.full(n - 1, 50.0), 52.5])                       # and the dollar up 5%
    egx = series(np.r_[np.full(n - 1, 30000.0), 33000.0])
    rows = {x["key"]: x for x in gold.changes(gold.combine(g, r), gold.by_day(egx))}
    assert list(rows) == ["day", "week", "month", "quarter", "year", "ytd"]
    day = rows["day"]
    assert (day["usd"], day["rate"], day["egp"], day["stocks"]) == (10.0, 5.0, 15.5, 10.0)      # 1.10 x 1.05 = 1.155
    assert rows["month"]["egp"] == 15.5 and rows["year"]["from"] < rows["month"]["from"] < day["from"]
    assert gold.changes(gold.combine(g, r))[0]["stocks"] is None        # without an index the comparison is left out
    short = gold.changes(gold.combine(g.tail(30), r.tail(30)))
    assert [x["key"] for x in short if x["key"] in ("quarter", "year")] == []   # no price that far back


def test_explain_names_what_drove_the_move():
    row = lambda usd, rate: {"label": "1 month", "usd": usd, "rate": rate, "egp": round(((1 + usd / 100) * (1 + rate / 100) - 1) * 100, 2)}
    assert "mostly gold itself" in gold.explain(row(-6.9, 0.7)) and "fell 6.2%" in gold.explain(row(-6.9, 0.7))
    assert "mostly the pound" in gold.explain(row(1.0, 12.0)) and "rose 13.1%" in gold.explain(row(1.0, 12.0))
    assert "both gold and the pound" in gold.explain(row(5.0, 4.0))
    assert "barely moved" in gold.explain(row(0.01, 0.02))


def test_trend_reads_the_averages_and_the_one_year_range():
    up = gold.trend(pd.Series(np.linspace(100, 200, 300)))
    assert up["trend"] == "Uptrend" and up["above50"] and up["above200"] and up["offHighPct"] == 0.0 and up["vs200Pct"] > 0
    down = gold.trend(pd.Series(np.linspace(200, 100, 300)))
    assert down["trend"] == "Downtrend" and down["offHighPct"] < -30 and down["low"] == 100.0
    assert gold.trend(pd.Series(np.linspace(100, 110, 40)))["trend"] == "Too little history"


def test_build_puts_it_together():
    n = 300
    out = gold.build(series(np.linspace(2000, 2600, n), tz="America/New_York", hour=-6), series(np.linspace(48, 52, n)),
                     series(np.linspace(30000, 33000, n), tz="Africa/Cairo", hour=10), points=50)
    assert out["prices"]["gram"]["21"] == pytest.approx(2600 * 52 / gold.OUNCE_GRAMS * 0.875, abs=0.01)
    assert out["previous"]["ounceUsd"] < out["prices"]["ounceUsd"] and out["goldDate"] == out["rateDate"]
    assert len(out["series"]) == 50 and out["series"][-1]["gram24"] == pytest.approx(out["prices"]["gram"]["24"], abs=0.01)
    assert out["trendEgp"]["trend"] == "Uptrend" and out["explain"] and out["changes"][0]["stocks"] is not None
    with pytest.raises(ValueError):
        gold.build(series([2000.0]), series([50.0]))


def test_endpoint_falls_back_to_another_gold_source_and_works_without_the_index(client, monkeypatch):
    n = 300
    data = {"OANDA:XAUUSD": series(np.linspace(2000, 2600, n)), "USDEGP": series(np.linspace(48, 52, n))}
    asked = []

    def fetch(symbol, interval, period):
        asked.append(symbol)
        if symbol not in data:
            raise SymbolNotFound(symbol)
        return symbol, pd.DataFrame({"Close": data[symbol]}), {}

    monkeypatch.setattr(main, "fetch_ohlcv", fetch)
    r = client.get("/api/gold")
    d = r.json()
    assert r.status_code == 200 and d["source"] == "OANDA:XAUUSD" and asked[:2] == ["TVC:GOLD", "OANDA:XAUUSD"]
    assert d["changes"][0]["stocks"] is None and d["prices"]["gram"]["21"] > 0     # EGX 30 didn't load: no comparison, still a page
    data.pop("USDEGP")
    assert client.get("/api/gold").status_code == 502
    data.clear()
    assert client.get("/api/gold").status_code == 502
    assert client.get("/gold").status_code == 200
