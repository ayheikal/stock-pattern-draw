"""Daily profile backups: written on save, pruned, mirrored, restorable."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import backup, main, profile


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    return TestClient(main.app), tmp_path


def test_save_writes_todays_backup_and_restores(client):
    c, tmp = client
    c.put("/api/profile/entries", json={"symbol": "COMI.CA", "expectation": "bullish"})
    today = tmp / "backups" / f"profile-{datetime.now():%Y-%m-%d}.json"
    assert today.exists() and "COMI.CA" in today.read_text(encoding="utf-8")
    listing = c.get("/api/backups").json()
    assert listing["files"][0]["name"] == today.name and listing["last"]

    c.put("/api/profile/entries", json={"symbol": "TMGH.CA"})  # a later edit I want to undo
    old = tmp / "backups" / "profile-2026-01-01.json"
    old.write_text(json.dumps({"entries": [{"symbol": "OLD.CA"}]}), encoding="utf-8")
    r = c.post("/api/backups/restore", params={"name": old.name})
    assert r.status_code == 200 and [e["symbol"] for e in r.json()["entries"]] == ["OLD.CA"]
    kept = [f["name"] for f in c.get("/api/backups").json()["files"] if "before-restore" in f["name"]]
    assert kept  # the profile I replaced is still there
    assert c.post("/api/backups/restore", params={"name": "../profile.json"}).status_code == 404


def test_damaged_backup_is_refused(client):
    c, tmp = client
    c.put("/api/profile/entries", json={"symbol": "COMI.CA"})
    (tmp / "backups" / "profile-2026-01-02.json").write_text('{"entries": [{"symbol": ""}]}', encoding="utf-8")
    with pytest.raises(Exception):
        profile.restore_backup("profile-2026-01-02.json")
    assert profile.load().entries[0].symbol == "COMI.CA"


def test_keeps_last_30_days_and_mirrors(tmp_path):
    src = tmp_path / "profile.json"
    src.write_text("{}", encoding="utf-8")
    d = tmp_path / "backups"
    d.mkdir()
    for i in range(35):
        (d / f"profile-{datetime(2026, 1, 1) + timedelta(days=i):%Y-%m-%d}.json").write_text("{}", encoding="utf-8")
    mirror = tmp_path / "OneDrive" / "PatternCharts"
    backup.snapshot(src, str(mirror))
    assert len(list(d.glob("profile-????-??-??.json"))) == 30
    assert (mirror / f"profile-{datetime.now():%Y-%m-%d}.json").exists()


def test_export_leaves_out_the_token(client):
    c, _ = client
    c.put("/api/profile/settings", json={"telegramToken": "123:secret", "telegramChatId": "1"})
    r = c.get("/api/profile/export")
    assert "attachment" in r.headers["content-disposition"] and "secret" not in r.text
