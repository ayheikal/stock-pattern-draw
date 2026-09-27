"""EGX market snapshot: breadth, sectors, movers and the live watchlists (no network)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import egx, main, screener


def stock(name, sector, change, value, close=10.0, cap=1e9, sma50=9.0, sma200=11.0):
    return {"symbol": f"{name}.CA", "name": name, "sector": sector, "close": close, "changePct": change, "volume": 1.0,
            "value": value, "marketCap": cap, "sma50": sma50, "sma200": sma200}


MARKET = {"asOf": 1, "stocks": [
    stock("BIG", "Finance", 2.0, 50e6, cap=9e9),
    stock("SMALL", "Finance", -4.0, 2e6, cap=1e9),
    stock("MID", "Utilities", -1.0, 5e6, sma200=5.0),
    stock("TINY", "Utilities", 9.0, 5e5),       # too little traded to rank as a mover
    stock("HALT", "Other", 0.0, 0.0),           # suspended: not traded, ignored
]}


def test_overview_breadth_sectors_movers():
    o = egx.overview(MARKET, [])
    b = o["breadth"]
    assert (b["traded"], b["advancers"], b["decliners"], b["unchanged"]) == (4, 2, 2, 0)
    assert b["above50"] == 100.0 and b["above200"] == 25.0  # only MID is above its 200-day average
    fin = next(s for s in o["sectors"] if s["sector"] == "Finance")
    assert fin["changePct"] == round((9e9 * 2 - 1e9 * 4) / 10e9, 2)  # weighted by company size
    assert o["sectors"][0]["sector"] == "Finance"  # most traded first
    assert [m["symbol"] for m in o["gainers"]][0] == "BIG.CA" and "TINY.CA" not in [m["symbol"] for m in o["gainers"]]
    assert o["losers"][0]["symbol"] == "SMALL.CA"


def test_live_watchlists(monkeypatch):
    monkeypatch.setattr(egx, "fetch_market", lambda: MARKET)
    u = egx.universes(max_symbols=2)
    assert u["egx_top"]["symbols"] == ["BIG.CA", "SMALL.CA"]
    assert u["egx_sector_utilities"]["symbols"] == ["MID.CA", "TINY.CA"] and "egx_sector_other" not in u
    assert screener.get_universe("egx_sector_finance")["symbols"] == ["BIG.CA", "SMALL.CA"]
    assert screener.get_universe("egx")["label"].startswith("Egypt")  # built-ins need no network


def test_watchlists_fall_back_when_offline(monkeypatch):
    def boom():
        raise OSError("offline")
    monkeypatch.setattr(egx, "fetch_market", boom)
    assert set(screener.all_universes()) == set(screener.UNIVERSES)
    assert TestClient(main.app).get("/api/market").status_code == 502
