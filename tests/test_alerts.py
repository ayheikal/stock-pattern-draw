from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import alerts, main, profile


def cairo(y, m, d, hh, mm):
    return pd.Timestamp(datetime(y, m, d, hh, mm)).tz_localize("Africa/Cairo").to_pydatetime()


def test_session_hours():
    assert alerts.in_session(cairo(2026, 9, 27, 11, 0))  # Sunday
    assert alerts.in_session(cairo(2026, 10, 1, 14, 40))  # Thursday, near the close
    assert not alerts.in_session(cairo(2026, 10, 2, 11, 0))  # Friday
    assert not alerts.in_session(cairo(2026, 10, 3, 11, 0))  # Saturday
    assert not alerts.in_session(cairo(2026, 9, 27, 9, 30))  # before the open
    assert not alerts.in_session(cairo(2026, 9, 27, 15, 0))  # after the close
    assert not alerts.in_session(cairo(2026, 10, 6, 11, 0), [date(2026, 10, 6)])  # holiday


def live(action: str = "Cut loss", tone: str = "sell", attention: list | None = None) -> dict:
    return {
        "entries": [{"symbol": "ADIB", "action": {"action": action, "tone": tone, "group": "Sell", "reason": "Broke your stop."}},
                    {"symbol": "TMGH", "action": {"action": "Hold", "tone": "hold", "group": "Hold", "reason": "Fine."}}],
        "summary": {"attention": attention if attention is not None else
                    [{"symbol": "ADIB", "kind": "stop", "text": "Price is past your stop (60)."},
                     {"symbol": "ADIB", "kind": "disagree", "text": "Chart disagrees."}]},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("ALERTS_STATE", str(tmp_path / "alerts.json"))
    profile.set_settings(profile.Settings(telegramToken="t", telegramChatId="c", alertsOn=True))
    sent = []
    return lambda token, chat, text: sent.append(text), sent


def test_sends_only_new_items(setup):
    send, sent = setup
    r = alerts.check_once(send, live())
    assert r == {"ok": True, "sent": 2, "active": 2}  # stop + the Cut loss action; "disagree" and plain Hold stay quiet
    assert "ADIB" in sent[0] and "Cut loss" in sent[0] and "TMGH" not in sent[0]
    assert alerts.check_once(send, live())["sent"] == 0 and len(sent) == 1  # nothing new, no message


def test_changed_action_is_sent_again(setup):
    send, sent = setup
    alerts.check_once(send, live())
    r = alerts.check_once(send, live(action="Take profit", attention=[]))
    assert r["sent"] == 1 and "Take profit" in sent[-1]


def test_missing_telegram_setup_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("ALERTS_STATE", str(tmp_path / "alerts.json"))
    r = alerts.check_once(lambda *a: None, live())
    assert not r["ok"] and "Telegram" in r["error"]
    assert "Telegram" in alerts.load_state()["lastError"]


def test_alert_endpoints(setup, monkeypatch):
    send, sent = setup
    monkeypatch.setattr(alerts, "send_telegram", send)
    monkeypatch.setattr(alerts.profile, "live_profile", lambda i, p: live())
    client = TestClient(main.app)
    assert client.post("/api/alerts/test").json() == {"ok": True} and "test" in sent[-1]
    assert client.post("/api/alerts/check").json()["sent"] == 2
    st = client.get("/api/alerts/status").json()
    assert st["on"] and st["configured"] and st["lastSent"] and st["active"] == 2
