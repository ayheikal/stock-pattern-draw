"""Bonus shares, dividends, costs and liquidity in the profile (EGX specifics)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main, profile
from app.sizing import size_position

# COMI's real history (Yahoo): 10% bonus shares on 2025-12-17, then a 6 EGP dividend.
COMI_ACTIONS = [
    {"date": "2024-04-22", "dividend": 0.5, "split": 0.0},
    {"date": "2025-12-17", "dividend": 0.0, "split": 1.1},
    {"date": "2026-04-07", "dividend": 6.0, "split": 0.0},
]


def live(last: float, **extra) -> dict:
    return {"symbol": "COMI.CA", "name": "CIB", "currency": "EGP", "last": last, "changePct": 0.0, "bias": "Neutral",
            "score": 0.0, "upTarget": last * 1.1, "downTarget": last * 0.9, "action": "Wait", "signal": ""} | extra


def held(**kw) -> profile.Entry:
    return profile.Entry(**({"symbol": "COMI.CA", "position": "long", "qty": 100, "avgCost": 110, "since": "2025-06-01"} | kw))


def test_bonus_shares_raise_qty_and_lower_cost():
    c = profile.corporate(held(), COMI_ACTIONS, dividend_tax_pct=10)
    assert c["factor"] == 1.1
    assert [e["kind"] for e in c["events"]] == ["bonus", "dividend"]  # the 2024 dividend is before my buy date
    # 6 EGP on 110 shares = 660 gross, 594 after 10% tax
    assert (c["dividendsGross"], c["dividends"]) == (660.0, 594.0)


def test_compare_uses_adjusted_position():
    s = profile.Settings(feePct=0, dividendTaxPct=10)
    out = profile.compare(held(), live(128.2), settings=s, actions=COMI_ACTIONS)
    assert (out["adjQty"], out["adjAvgCost"]) == (110.0, 100.0)
    assert (out["qty"], out["avgCost"]) == (100, 110)  # what I typed is kept
    # without the bonus adjustment this would look like +16.5%; really it's +28.2%
    assert out["plPct"] == 28.2 and out["plValue"] == pytest.approx(3102.0)
    assert out["income"] == 594.0 and out["totalReturn"] == pytest.approx(3696.0)


def test_buy_date_after_the_bonus_changes_nothing():
    out = profile.compare(held(since="2026-01-05", avgCost=100, qty=110), live(128.2),
                          settings=profile.Settings(feePct=0), actions=COMI_ACTIONS)
    assert out["adjQty"] == 110 and out["corporate"]["factor"] == 1.0


def test_missing_buy_date_asks_for_it():
    out = profile.compare(held(since=None), live(128.2), settings=profile.Settings(feePct=0), actions=COMI_ACTIONS)
    assert out["corporate"]["needsDate"] and out["adjQty"] == 100
    kinds = {a["kind"] for a in profile.summarize([out], profile.Settings(feePct=0))["attention"]}
    assert "date" in kinds


def test_fees_and_break_even():
    s = profile.Settings(feePct=0.5, gainsTaxPct=0)
    out = profile.compare(held(since=None, avgCost=100), live(100.0), settings=s)
    # flat price still loses both commissions: (100*0.995 - 100*1.005) / 100.5
    assert out["plPct"] == pytest.approx(-0.995, abs=0.01)  # shown to 2 decimals
    assert out["breakEven"] == pytest.approx(100 * 1.005 / 0.995, abs=1e-3)


def test_gains_tax_only_on_gains():
    s = profile.Settings(feePct=0, gainsTaxPct=10)
    assert profile.compare(held(since=None, avgCost=100), live(120.0), settings=s)["plPct"] == 18.0
    assert profile.compare(held(since=None, avgCost=100), live(80.0), settings=s)["plPct"] == -20.0


def test_sizing_counts_fees():
    plain = size_position(100_000, 1, 25, 50, 45)
    with_fees = size_position(100_000, 1, 25, 50, 45, fee_pct=0.5)
    assert plain["shares"] == 200
    # risk per share 50*1.005 - 45*0.995 = 5.475 -> 182 shares
    assert with_fees["shares"] == 182 and with_fees["riskPct"] <= 1.0


def test_liquidity_caps_size_and_flags_thin_stocks():
    z = size_position(1_000_000, 1, 25, 50, 45, max_shares=300)
    assert (z["shares"], z["limitedBy"]) == (300, "liquidity")
    s = profile.Settings(capital=1_000_000, feePct=0, maxAdtvPct=5, minAdtv=1_000_000)
    # 200k EGP a day x 5% = 10k EGP -> 200 shares at 50
    out = profile.compare(profile.Entry(symbol="X", expectation="bullish", stop=45), live(50.0, adtv=200_000), settings=s)
    assert out["thin"] and out["sizing"]["shares"] == 200 and out["sizing"]["limitedBy"] == "liquidity"
    held_thin = profile.compare(profile.Entry(symbol="X", position="long", qty=10, avgCost=40, stop=45),
                                live(50.0, adtv=200_000), settings=s)
    assert "thin" in {a["kind"] for a in profile.summarize([held_thin], s)["attention"]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(profile, "_scan_one", lambda s, i, p: live(128.2) | {"symbol": s})
    monkeypatch.setattr(profile, "fetch_actions", lambda s: COMI_ACTIONS)
    monkeypatch.setattr(profile, "usd_rates", lambda: None)  # no network in tests
    return TestClient(main.app)


def test_live_profile_applies_actions_and_hides_token(client):
    client.put("/api/profile/settings", json={"feePct": 0, "telegramToken": "123:secret", "telegramChatId": "42"})
    client.put("/api/profile/entries", json={"symbol": "COMI.CA", "position": "long", "qty": 100, "avgCost": 110,
                                             "since": "2025-06-01"})
    body = client.get("/api/profile").json()
    e = body["entries"][0]
    assert (e["adjQty"], e["since"]) == (110.0, "2025-06-01")
    assert body["summary"]["byCurrency"][0]["income"] == 594.0
    # the token is stored but never sent back
    for resp in (body, client.get("/api/profile?live=false").json()):
        assert "telegramToken" not in resp["settings"] and resp["settings"]["hasTelegramToken"]
    # saving settings from the page (no token) keeps it; "clear" removes it
    client.put("/api/profile/settings", json={"feePct": 0.2})
    assert profile.load().settings.telegramToken == "123:secret"
    client.put("/api/profile/settings", json={"telegramToken": "clear"})
    assert profile.load().settings.telegramToken == ""
