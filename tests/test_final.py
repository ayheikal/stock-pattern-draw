"""Final decision: how the inputs combine into one call, and the endpoint (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import final, main, profile

READY = {"applicable": True, "verdict": "ready", "label": "Ready to buy", "reason": "Every check that has data passes.",
         "passed": 9, "known": 10}
WAIT = READY | {"verdict": "wait", "label": "Wait", "reason": "Results due soon."}
AVOID = READY | {"verdict": "avoid", "label": "Avoid", "reason": "Below its 200-day average."}


def rating(key: str) -> dict:
    labels = {"strong-buy": "Strong buy", "buy": "Buy", "hold": "Hold", "reduce": "Reduce", "sell": "Sell"}
    return {"fundamental": {"label": "Average"}, "technical": {"label": "Mixed"},
            "signal": {"key": key, "label": labels[key], "text": "", "notes": []}}


def value(verdict: str, discount: float = 30.0) -> dict:
    labels = {"buy": "Buy zone", "short": "Not cheap enough", "dear": "Above fair value"}
    return {"stock": {"verdict": verdict, "label": labels[verdict], "text": "", "price": 10.0, "fair": 10 / (1 - discount / 100),
                      "discountPct": discount}}


def call(ck=READY, rate="hold", look=None, mood="neutral", halal=None, held=None, action=None):
    card = action and {"action": action}
    evidence = [final.ev_plan(card, held is not None), final.ev_checklist(ck), final.ev_rating(rating(rate)),
                final.ev_value(look), final.ev_market({"key": mood, "text": ""}), final.ev_halal(halal)]
    return final.decide(evidence, ck, held)


def act(action: str, group: str) -> dict:
    return {"action": action, "group": group, "reason": f"{action} because of my plan."}


@pytest.mark.parametrize("kwargs, expect", [
    (dict(), "buy"),                                                              # Ready, nothing for or against
    (dict(rate="buy", look=value("buy")), "strong-buy"),                         # two for
    (dict(rate="buy", look=value("buy"), mood="weak"), "buy"),                   # two for, one against
    (dict(look=value("dear", -20)), "wait"),                                     # Ready, but dear
    (dict(ck=WAIT, rate="strong-buy"), "wait"),                                  # the checklist gates any buy
    (dict(ck=AVOID, rate="strong-buy"), "dont-buy"),
    (dict(rate="sell"), "dont-buy"),
    (dict(halal={"status": "not", "source": "mine"}), "dont-buy"),               # my own not-halal tag blocks a buy
    (dict(ck=READY | {"verdict": "unknown", "label": "Not enough data"}), "unknown"),
])
def test_not_held(kwargs, expect):
    assert call(**kwargs)["key"] == expect


@pytest.mark.parametrize("kwargs, expect, label", [
    (dict(action=act("Cut loss", "Sell"), rate="strong-buy"), "sell", "Cut loss"),   # my stop outranks every analysis
    (dict(action=act("Trim", "Sell")), "reduce", "Trim"),
    (dict(action=act("Hold", "Hold"), rate="sell"), "sell", "Sell"),
    (dict(action=act("Hold", "Hold"), ck=AVOID, look=value("dear", -20)), "reduce", "Reduce"),
    (dict(action=act("Hold", "Hold"), look=value("dear", -20)), "hold", "Hold"),     # one against: keep, don't add
    (dict(action=act("Add more", "Buy")), "add", "Add more"),
    (dict(action=act("Add more", "Buy"), mood="weak"), "hold", "Hold"),
])
def test_held(kwargs, expect, label):
    out = call(held="long", **kwargs)
    assert (out["key"], out["label"]) == (expect, label)


def test_market_mood():
    assert final.market_mood({"trend": "Uptrend"}, {"above200": 60.0})["key"] == "strong"
    assert final.market_mood({"trend": "Uptrend"}, {"above200": 30.0})["key"] == "neutral"   # the index alone isn't enough
    assert final.market_mood({"trend": "Downtrend"}, {"above200": 35.0})["key"] == "weak"
    assert final.market_mood({"trend": "Too little history"}, None)["key"] == "unknown"


def live(symbol: str, currency: str | None = "EGP") -> dict:
    return {"symbol": symbol, "query": symbol, "name": f"{symbol} name", "currency": currency, "last": 100.0, "changePct": 1.0,
            "bias": "Neutral", "score": 0.0, "upTarget": 130.0, "downTarget": 90.0, "chartStop": 90.0, "action": "Wait",
            "signal": "", "aboveSma200": True, "adtv": 5e6, "lastBar": "2026-10-29", "tech": None}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_DIR", str(tmp_path))
    monkeypatch.setattr(final, "_analyze_one", lambda s, i, p: live("EGX:" + s.removesuffix(".CA") if s != "EGX30" else "EGX:EGX30",
                                                                    None if s == "EGX30" else "EGP"))
    monkeypatch.setattr(profile, "fetch_actions", lambda s: [])
    monkeypatch.setattr(main, "_index_card", lambda *a: {"trend": "Uptrend"})
    return TestClient(main.app)


def test_endpoint_combines_everything_offline(client):
    client.put("/api/profile/entries", json={"symbol": "COMI.CA", "position": "long", "qty": 10, "avgCost": 90, "stop": 85})
    b = client.get("/api/final?symbol=COMI.CA").json()
    assert b["position"] == "long" and b["profileSymbol"] == "COMI.CA" and b["holding"]["adjQty"] == 10
    assert [e["key"] for e in b["evidence"]] == ["plan", "checklist", "rating", "value", "market", "chart", "halal"]
    assert b["decision"]["group"] in ("Hold", "Sell", "Buy") and b["checks"]
    # financials are offline in tests: fair value says why instead of guessing
    assert next(e for e in b["evidence"] if e["key"] == "value")["stance"] == "unknown"


def test_endpoint_index_and_errors(client):
    b = client.get("/api/final?symbol=EGX30").json()
    assert b["decision"]["key"] == "index" and [e["key"] for e in b["evidence"]] == ["plan", "checklist", "chart"]
    assert client.get("/api/final?symbol=COMI&interval=1h").status_code == 400
