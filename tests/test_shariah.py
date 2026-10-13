from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import egx, main, profile, shariah

MARKET = [
    {"symbol": "COMI.CA", "name": "CIB", "isin": "EGS60121C018"},
    {"symbol": "ABUK.CA", "name": "Abu Qir Fertilizers", "isin": "EGS38191C010"},
    {"symbol": "EFID.CA", "name": "Edita Food", "isin": "EGS305I1C011"},
]


def test_key_joins_spellings():
    assert shariah.key("egx:abuk") == shariah.key(" ABUK.CA ") == "ABUK.CA"
    assert shariah.key("AAPL") == "AAPL"


def test_status_my_tag_wins_over_list():
    sh = profile.Shariah(symbols=["ABUK.CA", "EFID.CA"], listDate=date(2026, 7, 1), tags={"EFID.CA": "not", "AAPL": "halal"})
    assert shariah.status(sh, "EGX:ABUK") == {"status": "halal", "source": "list", "listDate": "2026-07-01"}
    assert shariah.status(sh, "EFID.CA") == {"status": "not", "source": "mine"}
    assert shariah.status(sh, "AAPL") == {"status": "halal", "source": "mine"}
    assert shariah.status(sh, "COMI.CA") is None  # off the list isn't "not halal"
    assert shariah.halal_symbols(sh) == ["ABUK.CA", "AAPL"]


def test_parse_plain_list():
    assert shariah.parse_list("abuk, EFID.CA\nEGX:COMI  XXXX", MARKET) == (["ABUK.CA", "EFID.CA", "COMI.CA"], ["XXXX"])


def test_parse_table_ignores_company_names():
    table = "Abu Qir Fertilizers\tABUK.CA\tEGS38191C010\nEdita Food Industries\tEGS305I1C011\nGone Co\tEGS00000Z000"
    assert shariah.parse_list(table, MARKET) == (["ABUK.CA", "EFID.CA"], ["EGS00000Z000"])


def test_parse_offline_takes_codes_as_typed():
    assert shariah.parse_list("ABUK.CA, EFID", None) == (["ABUK.CA", "EFID.CA"], [])


def test_stale_after_six_months():
    assert not shariah.is_stale(date(2026, 7, 1), date(2026, 10, 14))
    assert shariah.is_stale(date(2026, 1, 1), date(2026, 10, 14))
    assert not shariah.is_stale(None, date(2026, 10, 14))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setattr(egx, "fetch_market", lambda: {"asOf": 1, "stocks": MARKET})
    return TestClient(main.app)


def test_api_list_and_tags(client):
    assert client.get("/api/shariah").json() == {"symbols": [], "listDate": None, "stale": False, "tags": []}

    b = client.put("/api/shariah/list", json={"text": "ABUK.CA EFID.CA NOPE.CA", "listDate": "2026-07-01"}).json()
    assert [x["symbol"] for x in b["symbols"]] == ["ABUK.CA", "EFID.CA"]
    assert (b["symbols"][0]["name"], b["listDate"], b["unknown"], b["checked"]) == ("Abu Qir Fertilizers", "2026-07-01", ["NOPE.CA"], True)
    assert client.put("/api/shariah/list", json={"text": "hello"}).status_code == 400

    b = client.put("/api/shariah/tag", json={"symbol": "EGX:EFID", "status": "not"}).json()
    assert b["halal"] == {"status": "not", "source": "mine"}
    assert b["tags"] == [{"symbol": "EFID.CA", "name": "Edita Food", "status": "not"}]
    assert client.put("/api/shariah/tag", json={"symbol": "EFID.CA", "status": "maybe"}).status_code == 400
    b = client.put("/api/shariah/tag", json={"symbol": "EFID.CA", "status": None}).json()
    assert b["halal"]["source"] == "list" and b["tags"] == []

    # The list survives in the saved profile and empties on clear.
    assert profile.load().shariah.symbols == ["ABUK.CA", "EFID.CA"]
    b = client.put("/api/shariah/list", json={"text": ""}).json()
    assert (b["symbols"], b["listDate"]) == ([], None)


def test_screen_marks_and_filters_halal(client, monkeypatch):
    rows = [{"symbol": s, "currency": "EGP", "score": 1.5} for s in ("EGX:ABUK", "EGX:COMI")]
    monkeypatch.setattr(main, "scan", lambda *a: {"scanned": 2, "skipped": [], "results": [dict(r) for r in rows]})
    client.put("/api/shariah/list", json={"text": "ABUK.CA"})

    res = client.get("/api/screen?symbols=ABUK,COMI").json()["results"]
    assert [(r["symbol"], (r["halal"] or {}).get("status")) for r in res] == [("EGX:ABUK", "halal"), ("EGX:COMI", None)]
    res = client.get("/api/screen?symbols=ABUK,COMI&halal_only=true").json()["results"]
    assert [r["symbol"] for r in res] == ["EGX:ABUK"]


def test_halal_watchlist(client):
    assert "egx_shariah" not in client.get("/api/universes").json()
    client.put("/api/shariah/list", json={"text": "ABUK.CA, EFID.CA"})
    client.put("/api/shariah/tag", json={"symbol": "EFID.CA", "status": "not"})
    client.put("/api/shariah/tag", json={"symbol": "COMI.CA", "status": "halal"})
    from app.screener import get_universe
    assert get_universe("egx_shariah")["symbols"] == ["ABUK.CA", "COMI.CA"]
