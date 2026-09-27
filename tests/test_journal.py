"""Trade history: average cost, realized P/L, fees, bonus shares and dividends between trades."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main, profile

T = profile.Trade
FEE02 = profile.Settings(feePct=0.2, dividendTaxPct=10, gainsTaxPct=0)
COMI_ACTIONS = [  # Yahoo style: the 2025-04 dividend is already divided by the later 1.1 bonus
    {"date": "2024-04-22", "dividend": 0.5, "split": 0.0},
    {"date": "2025-04-08", "dividend": 2.272727, "split": 0.0},
    {"date": "2025-12-17", "dividend": 0.0, "split": 1.1},
    {"date": "2026-04-07", "dividend": 6.0, "split": 0.0},
]


def test_average_cost_and_realized():
    j = profile.ledger([T(date="2026-01-05", side="buy", qty=100, price=10),
                        T(date="2026-02-05", side="buy", qty=100, price=12),
                        T(date="2026-03-05", side="sell", qty=50, price=15)], [], FEE02)
    # cost incl. fees 2204.40 for 200 -> 50 sold: basis 551.10; proceeds 750 - 1.50 fee
    assert j["realized"] == pytest.approx(197.40, abs=0.01)
    assert j["qty"] == 150 and j["avgCost"] == 11.0  # price-only average (fees are added back in net P/L)
    assert j["fees"] == pytest.approx(2 + 2.4 + 1.5)
    assert [y["year"] for y in j["years"]] == ["2026"]


def test_bonus_and_dividends_between_trades():
    j = profile.ledger([T(date="2025-01-02", side="buy", qty=100, price=110)], COMI_ACTIONS, FEE02)
    assert (j["qty"], j["avgCost"]) == (110, 100)
    # 2025-04: 2.50 EGP really paid on 100 shares; 2026-04: 6 EGP on 110 shares; both after 10% tax
    assert j["dividends"] == pytest.approx((250 + 660) * 0.9, abs=0.01)
    assert [e["kind"] for e in j["events"]] == ["dividend", "bonus", "dividend"]
    assert j["events"][0]["perShare"] == pytest.approx(2.5, abs=1e-3)
    years = {y["year"]: y for y in j["years"]}
    assert years["2025"]["dividends"] == pytest.approx(225.0, abs=0.01)


def test_sell_after_bonus_uses_the_new_share_count():
    j = profile.ledger([T(date="2025-06-01", side="buy", qty=100, price=110, fees=0),
                        T(date="2026-05-01", side="sell", qty=110, price=130, fees=0)], COMI_ACTIONS, FEE02)
    assert j["qty"] == 0 and j["avgCost"] is None and not j["warnings"]
    assert j["realized"] == pytest.approx(110 * 130 - 100 * 110)  # 3300 profit on the whole position


def test_overselling_is_capped_and_reported():
    j = profile.ledger([T(date="2026-01-05", side="buy", qty=10, price=10, fees=0),
                        T(date="2026-01-06", side="sell", qty=15, price=11, fees=0)], [], FEE02)
    assert j["qty"] == 0 and j["realized"] == pytest.approx(10.0) and "more than" in j["warnings"][0]


def live(last: float) -> dict:
    return {"symbol": "COMI.CA", "name": "CIB", "currency": "EGP", "last": last, "changePct": 0.0, "bias": "Neutral",
            "score": 0.0, "upTarget": last * 1.1, "downTarget": last * 0.9, "action": "Wait", "signal": ""}


def test_compare_with_trades_and_closed_position():
    e = profile.Entry(symbol="COMI.CA", position="none", trades=[T(date="2025-06-01", side="buy", qty=100, price=110)])
    out = profile.compare(e, live(128.2), settings=FEE02, actions=COMI_ACTIONS)
    assert out["position"] == "long" and (out["adjQty"], out["adjAvgCost"]) == (110, 100) and out["since"] == "2025-06-01"
    closed = profile.Entry(symbol="COMI.CA", trades=[T(date="2025-06-01", side="buy", qty=100, price=110),
                                                   T(date="2026-05-01", side="sell", qty=110, price=130)])
    out = profile.compare(closed, live(128.2), settings=FEE02, actions=COMI_ACTIONS)
    assert out["position"] == "none" and out["plPct"] is None and out["journal"]["realized"] > 0
    years = profile.summarize([out], FEE02)["years"]
    assert {y["year"] for y in years} == {"2025", "2026"} and all(y["currency"] == "EGP" for y in years)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(profile, "_scan_one", lambda s, i, p: live(128.2) | {"symbol": s})
    monkeypatch.setattr(profile, "fetch_actions", lambda s: COMI_ACTIONS)
    monkeypatch.setattr(profile, "usd_rates", lambda: None)  # no network in tests
    return TestClient(main.app)


def test_trade_endpoints(client):
    client.put("/api/profile/entries", json={"symbol": "COMI.CA", "expectation": "bullish"})
    assert client.post("/api/profile/entries/COMI.CA/trades", json={"date": "2025-06-01", "side": "buy", "qty": 100, "price": 110}).status_code == 200
    assert client.post("/api/profile/entries/COMI.CA/trades", json={"date": "2025-01-01", "side": "buy", "qty": 0, "price": 1}).status_code == 422
    assert client.post("/api/profile/entries/NOPE/trades", json={"date": "2025-06-01", "side": "buy", "qty": 1, "price": 1}).status_code == 404
    # editing the entry from the form keeps its trades
    client.put("/api/profile/entries", json={"symbol": "COMI.CA", "expectation": "bullish", "stop": 120})
    e = client.get("/api/profile").json()["entries"][0]
    assert len(e["trades"]) == 1 and e["position"] == "long" and e["adjQty"] == 110 and e["stop"] == 120
    assert client.delete("/api/profile/entries/COMI.CA/trades/0").status_code == 200
    assert client.delete("/api/profile/entries/COMI.CA/trades/0").status_code == 404
    assert client.get("/api/profile").json()["entries"][0]["trades"] == []


# EGP per USD: 30.9 when I bought, 51.7 now (the pound lost ~40%)
RATES = pd.Series([30.9, 48.0, 51.7], index=pd.to_datetime(["2025-01-01", "2025-06-01", "2026-09-24"]))


def test_usd_view_simple_entry():
    fx = profile.fx_lookup(RATES)
    assert fx("2025-03-15") == 30.9 and fx(None) == 51.7
    e = profile.Entry(symbol="COMI.CA", position="long", qty=100, avgCost=50, since="2025-02-01")
    out = profile.compare(e, live(80.0), settings=profile.Settings(feePct=0), fx=fx)
    u = out["usd"]
    # +60% in pounds, but in dollars: 8000/51.7 vs 5000/30.9 -> about -4.4%
    assert out["plPct"] == 60.0
    assert u["plUsdPct"] == pytest.approx((8000 / 51.7) / (5000 / 30.9) * 100 - 100, abs=0.01)
    assert u["egpChangePct"] == pytest.approx((30.9 / 51.7 - 1) * 100, abs=0.01)


def test_usd_view_from_trades_uses_each_buy_date():
    fx = profile.fx_lookup(RATES)
    e = profile.Entry(symbol="X", trades=[T(date="2025-02-01", side="buy", qty=100, price=50, fees=0),
                                          T(date="2025-07-01", side="buy", qty=100, price=60, fees=0)])
    out = profile.compare(e, live(80.0), settings=profile.Settings(feePct=0), fx=fx)
    cost_usd = 5000 / 30.9 + 6000 / 48.0
    assert out["usd"]["costUsd"] == pytest.approx(cost_usd, abs=0.01)
    assert out["usd"]["valueUsd"] == pytest.approx(16000 / 51.7, abs=0.01)


def test_no_usd_view_for_non_egp_or_watching():
    fx = profile.fx_lookup(RATES)
    assert profile.compare(profile.Entry(symbol="X", expectation="bullish"), live(80.0), fx=fx)["usd"] is None
    usd_stock = live(80.0) | {"currency": "USD"}
    e = profile.Entry(symbol="AAPL", position="long", qty=1, avgCost=50, since="2025-02-01")
    assert profile.compare(e, usd_stock, fx=fx)["usd"] is None
