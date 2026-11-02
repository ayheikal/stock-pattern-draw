from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main, price_alerts, profile


def bars(rows: list[tuple[float, float, float]], start: pd.Timestamp) -> pd.DataFrame:
    """Hourly (high, low, close) bars from `start`."""
    idx = pd.date_range(start, periods=len(rows), freq="h")
    return pd.DataFrame({"Open": [c for *_, c in rows], "High": [h for h, _, _ in rows], "Low": [l for _, l, _ in rows],
                         "Close": [c for *_, c in rows], "Volume": 1.0}, index=idx)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICE_ALERTS", str(tmp_path / "price_alerts.json"))
    profile.set_settings(profile.Settings(telegramToken="t", telegramChatId="c"))
    sent = []
    return (lambda token, chat, text: sent.append(text)), sent


NOW = pd.Timestamp.now(tz="UTC")
BEFORE, AFTER = NOW - pd.Timedelta(hours=5), NOW + pd.Timedelta(hours=1)


def test_direction_follows_the_side_of_the_price(setup):
    up = price_alerts.add(price_alerts.NewAlert(symbol="EGX:COMI", price=110, note="breakout"), last=100.0)
    down = price_alerts.add(price_alerts.NewAlert(symbol="EGX:COMI", price=90), last=100.0)
    assert (up["direction"], down["direction"]) == ("above", "below")
    assert [a["id"] for a in price_alerts.for_symbol("EGX:COMI")] == [up["id"], down["id"]]
    assert price_alerts.for_symbol("AAPL") == []


def test_hit_by_the_last_price_or_a_touch_after_it_was_set(setup):
    a = price_alerts.add(price_alerts.NewAlert(symbol="X", price=110), last=100.0)
    assert price_alerts.hit_price(a, bars([(105, 99, 104)], AFTER)) is None
    assert price_alerts.hit_price(a, bars([(112, 108, 111)], AFTER)) == 111  # trading above it now
    # touched 111 an hour after the alert was set, then fell back: still a hit
    assert price_alerts.hit_price(a, bars([(111, 103, 104), (105, 101, 102)], AFTER)) == 111
    # a touch before the alert existed doesn't count
    assert price_alerts.hit_price(a, bars([(115, 100, 101), (103, 100, 102)], BEFORE)) is None
    b = price_alerts.add(price_alerts.NewAlert(symbol="X", price=90), last=100.0)
    assert price_alerts.hit_price(b, bars([(99, 89, 95)], AFTER)) == 89


def test_check_sends_once_and_keeps_the_rest_waiting(setup):
    send, sent = setup
    hit = price_alerts.add(price_alerts.NewAlert(symbol="X", price=110, name="Gold", note="take profit"), last=100.0)
    wait = price_alerts.add(price_alerts.NewAlert(symbol="X", price=130), last=100.0)
    fetch = lambda s: bars([(112, 108, 111)], AFTER)
    assert price_alerts.check_once(send, fetch) == {"checked": 2, "hit": 1, "sent": 1}
    assert sent == ["🔔 Gold: rose to 111.00, reaching your alert at 110.00\n📝 take profit"]
    by_id = {a["id"]: a for a in price_alerts.load()}
    assert by_id[hit["id"]]["triggeredPrice"] == 111 and by_id[hit["id"]]["sent"]
    assert by_id[wait["id"]]["triggeredAt"] is None
    assert price_alerts.check_once(send, fetch)["sent"] == 0 and len(sent) == 1  # a hit alert is done


def test_problems_are_kept_on_the_alert(setup):
    send, sent = setup
    a = price_alerts.add(price_alerts.NewAlert(symbol="X", price=110), last=100.0)

    def no_data(_s):
        raise OSError("offline")

    price_alerts.check_once(send, no_data)
    assert "offline" in price_alerts.load()[0]["error"] and price_alerts.load()[0]["triggeredAt"] is None
    profile.set_settings(profile.Settings())  # Telegram not set up
    price_alerts.check_once(send, lambda s: bars([(112, 108, 111)], AFTER))
    got = price_alerts.load()[0]
    assert got["triggeredAt"] and not got["sent"] and "Telegram isn't set up" in got["error"] and sent == []
    assert price_alerts.remove(a["id"]) and price_alerts.load() == [] and not price_alerts.remove(a["id"])


def test_api(setup, monkeypatch):
    df = bars([(101, 99, 100)], BEFORE)
    monkeypatch.setattr(price_alerts, "fetch_ohlcv", lambda s, i, p: (s, df, {}))
    client = TestClient(main.app)
    r = client.post("/api/price-alerts", json={"symbol": "EGX:COMI", "price": 95, "note": "buy zone"})
    assert r.status_code == 200 and r.json()["direction"] == "below" and r.json()["createdPrice"] == 100
    body = client.get("/api/price-alerts", params={"symbol": "EGX:COMI"}).json()
    assert body["telegram"] and [a["note"] for a in body["alerts"]] == ["buy zone"]
    assert client.post("/api/price-alerts", json={"symbol": "EGX:COMI", "price": -1}).status_code == 422
    assert client.delete(f"/api/price-alerts/{r.json()['id']}").json() == {"ok": True}
    assert client.delete(f"/api/price-alerts/{r.json()['id']}").status_code == 404
