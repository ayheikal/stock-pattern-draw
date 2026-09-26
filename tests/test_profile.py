from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main, profile


def live_row(symbol: str, last: float, score: float) -> dict:
    return {
        "symbol": symbol, "name": f"{symbol} name", "currency": "EGP", "last": last, "changePct": 1.0,
        "bias": "x", "score": score, "upTarget": last * 1.1, "downTarget": last * 0.9, "action": "Wait", "signal": "",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(profile, "_scan_one", lambda s, i, p: live_row(s, 110.0, 1.5))
    return TestClient(main.app)


def test_profile_crud(client):
    body = client.get("/api/profile?live=false").json()
    assert (body["name"], body["entries"], body["settings"]["capital"]) == ("", [], None)
    r = client.put("/api/profile/entries", json={"symbol": " egx30 ", "position": "long", "qty": 10, "avgCost": 100,
                                                 "expectation": "bullish", "target": 130, "stop": 90})
    assert r.status_code == 200
    client.put("/api/profile/entries", json={"symbol": "EGX30", "position": "long", "qty": 20, "avgCost": 100})
    client.put("/api/profile/name", json={"name": "Ahmed"})
    body = client.get("/api/profile?live=false").json()
    assert body["name"] == "Ahmed"
    assert [(e["symbol"], e["qty"]) for e in body["entries"]] == [("EGX30", 20)]

    assert client.delete("/api/profile/entries/egx30").status_code == 200
    assert client.delete("/api/profile/entries/EGX30").status_code == 404


def test_profile_rejects_bad_input(client):
    assert client.put("/api/profile/entries", json={"symbol": "X", "position": "maybe"}).status_code == 422
    assert client.put("/api/profile/entries", json={"symbol": "X", "avgCost": -1}).status_code == 422


def test_profile_live_comparison(client):
    client.put("/api/profile/entries", json={"symbol": "EGX30", "position": "long", "qty": 10, "avgCost": 100,
                                             "expectation": "bullish", "target": 130, "stop": 90})
    e = client.get("/api/profile").json()["entries"][0]
    assert e["plPct"] == 10.0 and e["plValue"] == 100.0
    assert e["agreement"] == "agrees" and e["status"] == "open"
    assert e["toTargetPct"] > 0 > e["toStopPct"]


@pytest.mark.parametrize("entry, last, score, expect", [
    (dict(position="short", avgCost=100, expectation="bearish", target=80, stop=115), 120.0, -1.0,
     {"plPct": -20.0, "status": "stop hit", "agreement": "agrees"}),
    (dict(expectation="bullish", target=105), 110.0, -1.0, {"status": "target hit", "agreement": "disagrees", "plPct": None}),
    (dict(expectation="neutral"), 110.0, 1.0, {"agreement": "mixed"}),
])
def test_compare_cases(entry, last, score, expect):
    out = profile.compare(profile.Entry(symbol="X", **entry), live_row("X", last, score))
    assert {k: out[k] for k in expect} == expect


def test_compare_reports_missing_data():
    out = profile.compare(profile.Entry(symbol="NOPE"), {"symbol": "NOPE", "error": "no data"})
    assert out["error"] == "no data" and out["symbol"] == "NOPE"


def test_summary_totals_and_attention():
    rows = [
        # long 10 @ 100, now 110 -> +100; chart bearish -> disagrees
        profile.compare(profile.Entry(symbol="A", position="long", qty=10, avgCost=100, expectation="bullish", stop=108),
                        live_row("A", 110.0, -1.0)),
        # short 5 @ 50, now 60 -> -50; past its stop
        profile.compare(profile.Entry(symbol="B", position="short", qty=5, avgCost=50, expectation="bearish", stop=55),
                        live_row("B", 60.0, -1.0)),
        profile.compare(profile.Entry(symbol="C", expectation="bullish", target=90), live_row("C", 95.0, 1.0)),
        profile.compare(profile.Entry(symbol="D"), {"symbol": "D", "error": "no data"}),
    ]
    s = profile.summarize(rows)
    assert (s["count"], s["holding"], s["watching"]) == (4, 2, 2)
    [book] = s["byCurrency"]
    assert (book["cost"], book["pl"], book["value"], book["positions"]) == (1250.0, 50.0, 1300.0, 2)
    assert s["best"]["symbol"] == "A" and s["worst"]["symbol"] == "B"
    assert s["expectation"] == {"bullish": 2, "neutral": 1, "bearish": 1}
    assert s["agreement"] == {"agrees": 2, "mixed": 0, "disagrees": 1}
    kinds = {(a["symbol"], a["kind"]) for a in s["attention"]}
    assert kinds == {("A", "near-stop"), ("A", "disagree"), ("B", "stop"), ("C", "target"), ("D", "error")}


def test_live_profile_includes_summary(client):
    client.put("/api/profile/entries", json={"symbol": "EGX30", "position": "long", "qty": 10, "avgCost": 100})
    s = client.get("/api/profile").json()["summary"]
    assert s["byCurrency"][0]["currency"] == "EGP" and s["byCurrency"][0]["pl"] == 100.0


def test_settings_sizing_and_risk(client):
    # no capital yet: cards explain instead of sizing
    client.put("/api/profile/entries", json={"symbol": "TMGH", "position": "long", "qty": 1000, "avgCost": 100, "stop": 95})
    e = client.get("/api/profile").json()["entries"][0]
    assert e["sizing"]["configured"] is False and e["exposure"] is None

    assert client.put("/api/profile/settings", json={"capital": 200000, "riskPct": 1, "maxPositionPct": 25}).status_code == 200
    assert client.put("/api/profile/settings", json={"capital": 1000, "riskPct": 50}).status_code == 422
    body = client.get("/api/profile").json()
    e = body["entries"][0]
    # last 110, my stop 95: 15/share at risk; 1% of 200k = 2000 -> 133 shares; 25% cap = 454 shares
    assert (e["sizing"]["shares"], e["sizing"]["limitedBy"], e["sizing"]["stopSource"]) == (133, "risk", "yours")
    # holding 1000 @ 110 = 110k = 55% of capital; stop at 95 -> 15k = 7.5% at risk
    assert (e["exposure"]["weightPct"], e["exposure"]["riskPct"]) == (55.0, 7.5)
    r = body["summary"]["risk"]
    assert (r["invested"], r["cash"], r["atRiskPct"]) == (110000.0, 90000.0, 7.5)
    kinds = {a["kind"] for a in body["summary"]["attention"] if a["symbol"] == "TMGH"}
    assert {"big", "risk"} <= kinds

    # the chart page's endpoint uses the same saved rules
    assert client.get("/api/size?entry=110&stop=95&currency=EGP").json()["shares"] == 133
    assert "USD" in client.get("/api/size?entry=110&stop=95&currency=USD").json()["reason"]
    assert "index" in client.get("/api/size?entry=110&stop=95").json()["reason"]


def test_held_position_stop_follows_the_position_not_the_view():
    # long ADIB expecting a fall, with a "stop" above the price: for the holding, that stop is already passed
    e = profile.Entry(symbol="ADIB", position="long", qty=300, avgCost=55, expectation="bearish", target=45, stop=60)
    out = profile.compare(e, live_row("ADIB", 51.0, 0.9))
    assert out["status"] == "stop hit"
    assert any(a["kind"] == "stop" for a in profile.summarize([out])["attention"])
