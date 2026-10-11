"""Sector outlook: Growth / Normal / Shallow from company financials plus my own views and events (no network)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import egx, main, profile

TODAY = date(2026, 10, 10)


def co(name, sector, rev=None, eps=None, cap=1e9, earnings=None, roe=None):
    ts = datetime(*earnings, tzinfo=timezone.utc).timestamp() if earnings else None
    return {"symbol": f"{name}.CA", "name": name, "sector": sector, "industry": "", "marketCap": cap,
            "revenueGrowth": rev, "epsGrowth": eps, "roe": roe, "netMargin": None, "pe": None, "dividendYield": None,
            "nextEarnings": ts, "perf3m": 5.0, "perf1y": 20.0}


STOCKS = [
    # booming: well above 12% inflation, every company's earnings up
    co("B1", "Finance", 40, 50, cap=9e9, earnings=(2026, 11, 1)), co("B2", "Finance", 35, 30), co("B3", "Finance", 30, 25),
    # shrinking: below inflation, most earnings falling
    co("S1", "Utilities", 2, -20, earnings=(2027, 6, 1)), co("S2", "Utilities", 5, -10), co("S3", "Utilities", 0, 4),
    # roughly keeping up with prices
    co("N1", "Transportation", 14, 12), co("N2", "Transportation", 12, -3), co("N3", "Transportation", 13, 15),
    # too few figures to judge
    co("U1", "Retail Trade", 50, 50), co("U2", "Retail Trade"), co("U3", "Retail Trade"),
    co("X1", None, 99, 99),  # no sector: ignored
]


def by_slug(out):
    return {r["slug"]: r for r in out["sectors"]}


def test_statuses_from_real_growth():
    out = egx.sector_outlook(STOCKS, 12.0, {}, TODAY)
    s = by_slug(out)
    assert (s["finance"]["status"], s["utilities"]["status"], s["transportation"]["status"], s["retail-trade"]["status"]) == \
        ("growth", "shallow", "normal", "unknown")
    assert s["finance"]["realRevenueGrowth"] == 23.0 and s["finance"]["growingPct"] == 100
    assert s["retail-trade"]["withData"] == 1 and "not enough" in s["retail-trade"]["reasons"][0]
    assert out["counts"] == {"growth": 1, "normal": 1, "shallow": 1, "unknown": 1}
    # no price data here, so the conclusion follows the financials alone; best verdict first
    assert [r["status"] for r in out["sectors"]] == ["growth", "normal", "unknown", "shallow"]
    assert s["finance"]["conclusion"]["verdict"] == "accumulate" and s["finance"]["technical"]["trend"] == "unknown"
    assert len(out["sectors"]) == 4  # the sector-less stock is left out


def test_high_inflation_turns_nominal_growth_shallow():
    s = by_slug(egx.sector_outlook(STOCKS, 40.0, {}, TODAY))
    assert s["transportation"]["status"] == "shallow"  # +13% looks fine until prices rise 40%


def test_my_view_wins_and_events_merge():
    views = {"finance": profile.SectorView(status="shallow", note="Rate cuts squeeze margins", events=[
        profile.SectorEvent(date=date(2026, 12, 25), title="CBE rate decision", impact="negative"),
        profile.SectorEvent(date=date(2026, 9, 1), title="Old news"),
    ])}
    s = by_slug(egx.sector_outlook(STOCKS, 12.0, views, TODAY))
    fin = s["finance"]
    assert (fin["status"], fin["auto"], fin["override"], fin["note"]) == ("shallow", "growth", True, "Rate cuts squeeze margins")
    assert [e["title"] for e in fin["events"]] == ["B1 results", "CBE rate decision"]  # sorted by date, past one moved out
    assert [e["title"] for e in fin["recent"]] == ["Old news"]
    assert s["utilities"]["events"] == []  # S1's results are more than 90 days away


def priced(stock, close, sma50, sma200, perf3m, rsi=50.0):
    return stock | {"close": close, "sma50": sma50, "sma200": sma200, "perf3m": perf3m, "perf1m": perf3m / 3, "rsi": rsi}


def test_technical_trend():
    up = [priced(co(f"U{i}", "X"), 12, 11, 10, 15) for i in range(3)]
    down = [priced(co(f"D{i}", "X"), 8, 9, 10, -12) for i in range(3)]
    assert egx.technical(up)["trend"] == "up" and egx.technical(up)["above200"] == 100
    assert egx.technical(down)["trend"] == "down"
    assert egx.technical(up[:2] + down[:2])["trend"] == "mixed"
    assert egx.technical(up[:2])["trend"] == "unknown"  # too few stocks with averages


@pytest.mark.parametrize("status, trend, verdict", [
    ("growth", "up", "favour"), ("growth", "down", "watch"), ("shallow", "up", "caution"),
    ("shallow", "down", "avoid"), ("normal", "mixed", "neutral"), ("unknown", "up", "watch"),
])
def test_conclusion_mixes_financials_and_trend(status, trend, verdict):
    assert egx.conclusion(status, {"trend": trend, "rsi": 50.0}, [])["verdict"] == verdict


def test_conclusion_notes_rsi_and_my_events():
    events = [{"source": "mine", "impact": "negative"}, {"source": "mine", "impact": "negative"},
              {"source": "earnings", "impact": "neutral"}]
    c = egx.conclusion("growth", {"trend": "up", "rsi": 75.0}, events)
    assert (c["verdict"], c["tone"]) == ("favour", "bullish")
    assert "Overbought" in c["notes"][0] and "lean negative" in c["notes"][1]


def test_sector_conclusion_in_outlook():
    stocks = [priced(s, 12, 11, 10, 15) if s["sector"] == "Finance" else s for s in STOCKS]
    s = by_slug(egx.sector_outlook(stocks, 12.0, {}, TODAY))
    assert s["finance"]["technical"]["trend"] == "up" and s["finance"]["conclusion"]["label"] == "Favour"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setattr(egx, "fetch_fundamentals", lambda: {"asOf": 1, "stocks": STOCKS})
    return TestClient(main.app)


def test_api_saves_my_view_and_inflation(client):
    b = client.get("/api/sectors").json()
    assert b["inflationPct"] == 12.0 and by_slug(b)["finance"]["status"] == "growth"

    view = {"status": "normal", "note": " watch the IMF review ", "events": [{"date": "2026-11-20", "title": "IMF review", "impact": "positive"}]}
    b = client.put("/api/sectors/finance", json=view).json()
    fin = by_slug(b)["finance"]
    assert (fin["status"], fin["note"], fin["updated"]) == ("normal", "watch the IMF review", date.today().isoformat())
    assert profile.load().sectors["finance"].events[0].title == "IMF review"  # kept in the profile, so it's backed up

    assert client.put("/api/sectors/finance", json={"status": None, "note": "", "events": []}).status_code == 200
    assert "finance" not in profile.load().sectors  # an empty view is removed

    assert client.put("/api/sectors/finance", json={"status": "booming"}).status_code == 422
    b = client.put("/api/sectors/settings/inflation", json={"inflationPct": 40}).json()
    assert b["inflationPct"] == 40 and by_slug(b)["transportation"]["status"] == "shallow"
    assert client.put("/api/sectors/settings/inflation", json={"inflationPct": "lots"}).status_code == 400
    assert client.get("/sectors").status_code == 200


def test_api_reports_fetch_failure(client, monkeypatch):
    def boom():
        raise OSError("offline")
    monkeypatch.setattr(egx, "fetch_fundamentals", boom)
    assert client.get("/api/sectors").status_code == 502
