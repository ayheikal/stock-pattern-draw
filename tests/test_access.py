"""Phone mode: other devices need the access key once; this computer never does."""
from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from app import access, main, serve


@pytest.fixture
def lan(tmp_path, monkeypatch):
    monkeypatch.setenv("PATTERN_LAN", "1")
    monkeypatch.setenv("PATTERN_KEY_FILE", str(tmp_path / "key.txt"))
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    return access.get_key()


def test_open_when_not_in_phone_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("PATTERN_LAN", raising=False)
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    assert access.get_key() is None
    assert TestClient(main.app).get("/api/profile?live=false").status_code == 200


def test_other_devices_need_the_key(lan):
    c = TestClient(main.app)  # the test client is not "this computer"
    assert c.get("/api/profile?live=false").status_code == 401
    page = c.get("/profile")
    assert page.status_code == 401 and "Access key" in page.text
    assert c.get("/manifest.webmanifest").status_code == 200  # installing needs no data
    assert c.get("/profile?key=wrong").status_code == 401


def test_key_link_logs_the_device_in(lan):
    c = TestClient(main.app)
    r = c.get(f"/profile?key={lan}&x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/profile?x=1"  # key removed from the address
    assert access.COOKIE in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert c.get("/api/profile?live=false").status_code == 200  # the cookie now opens everything
    assert c.get("/api/phone").status_code == 403  # the login link is only shown on the computer itself


def test_key_is_stable_and_local_is_always_allowed(lan):
    assert access.get_key() == lan and len(lan) >= 12
    assert access.allowed("127.0.0.1", "/api/profile", None, lan)
    assert not access.allowed("192.168.1.20", "/api/profile", None, lan)
    link = access.phone_link(8000)
    assert link["lan"] and link["url"].endswith(f"/profile?key={lan}")


def test_serve_refuses_a_busy_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert serve.port_busy(s.getsockname()[1])


def test_log_hides_the_key():
    import logging
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "GET %s HTTP/1.1" %d', ("1.2.3.4", "/profile?key=SECRET&x=1", 303), None)
    serve.HideKey().filter(rec)
    assert "SECRET" not in rec.getMessage() and "key=***&x=1" in rec.getMessage()
