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
    monkeypatch.setattr(profile, "fetch_actions", lambda s: [])
    monkeypatch.setattr(profile, "usd_rates", lambda: None)  # no network in tests
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


def test_adding_the_same_stock_twice_updates_one_entry(client):
    client.put("/api/profile/entries", json={"symbol": "ADIB", "target": 55})
    client.put("/api/profile/entries", json={"symbol": "ADIB.CA", "target": 60})
    client.put("/api/profile/entries", json={"symbol": "COMI.CA"})
    client.put("/api/profile/entries", json={"symbol": "COMI", "stop": 80})
    body = client.get("/api/profile?live=false").json()
    assert [(e["symbol"], e["target"], e["stop"]) for e in body["entries"]] == [("ADIB.CA", 60, None), ("COMI.CA", None, 80)]


def test_tidied_saves_bare_egx_codes_as_ca_and_merges_duplicates():
    t = profile.Trade(date="2026-10-05", side="buy", qty=184, price=42.6)
    entries = [profile.Entry(symbol="ADIB", qty=746, avgCost=43, target=55, trades=[t]),
               profile.Entry(symbol="AAPL"), profile.Entry(symbol="EGX30"),
               profile.Entry(symbol="ADIB.CA", note="from the chart", target=70)]
    resolved = {"ADIB": "EGX:ADIB", "AAPL": "AAPL", "EGX30": "EGX:EGX30", "ADIB.CA": "EGX:ADIB"}
    adib, aapl, egx30 = profile.tidied(entries, resolved)
    assert (adib.symbol, adib.qty, adib.target, adib.note, adib.trades) == ("ADIB.CA", 746, 55, "from the chart", [t])
    assert (aapl.symbol, egx30.symbol) == ("AAPL", "EGX30")  # not an EGX stock / an index alias
    assert profile.tidied([profile.Entry(symbol="AAPL"), profile.Entry(symbol="COMI.CA")],
                          {"AAPL": "AAPL", "COMI.CA": "EGX:COMI"}) is None


def test_live_profile_tidies_bare_egx_codes(client, monkeypatch):
    monkeypatch.setattr(profile, "_scan_one", lambda s, i, p: live_row("EGX:" + s.removesuffix(".CA"), 110.0, 1.5))
    client.put("/api/profile/entries", json={"symbol": "TMGH", "position": "long", "qty": 10, "avgCost": 100})
    assert [e["symbol"] for e in client.get("/api/profile").json()["entries"]] == ["TMGH.CA"]
    assert [e.symbol for e in profile.load().entries] == ["TMGH.CA"]


def test_profile_rejects_bad_input(client):
    assert client.put("/api/profile/entries", json={"symbol": "X", "position": "maybe"}).status_code == 422
    assert client.put("/api/profile/entries", json={"symbol": "X", "avgCost": -1}).status_code == 422


NOFEE = profile.Settings(feePct=0)


def test_profile_live_comparison(client):
    client.put("/api/profile/settings", json={"feePct": 0})
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
    out = profile.compare(profile.Entry(symbol="X", **entry), live_row("X", last, score), settings=NOFEE)
    assert {k: out[k] for k in expect} == expect


def test_compare_reports_missing_data():
    out = profile.compare(profile.Entry(symbol="NOPE"), {"symbol": "NOPE", "error": "no data"})
    assert out["error"] == "no data" and out["symbol"] == "NOPE"


def test_summary_totals_and_attention():
    rows = [
        # long 10 @ 100, now 110 -> +100; chart bearish -> disagrees
        profile.compare(profile.Entry(symbol="A", position="long", qty=10, avgCost=100, expectation="bullish", stop=108),
                        live_row("A", 110.0, -1.0), settings=NOFEE),
        # short 5 @ 50, now 60 -> -50; past its stop
        profile.compare(profile.Entry(symbol="B", position="short", qty=5, avgCost=50, expectation="bearish", stop=55),
                        live_row("B", 60.0, -1.0), settings=NOFEE),
        profile.compare(profile.Entry(symbol="C", expectation="bullish", target=90), live_row("C", 95.0, 1.0)),
        profile.compare(profile.Entry(symbol="D"), {"symbol": "D", "error": "no data"}),
    ]
    s = profile.summarize(rows, NOFEE)
    assert (s["count"], s["holding"], s["watching"]) == (4, 2, 2)
    [book] = s["byCurrency"]
    assert (book["cost"], book["pl"], book["value"], book["positions"]) == (1250.0, 50.0, 1300.0, 2)
    assert s["best"]["symbol"] == "A" and s["worst"]["symbol"] == "B"
    assert s["expectation"] == {"bullish": 2, "neutral": 1, "bearish": 1}
    assert s["agreement"] == {"agrees": 2, "mixed": 0, "disagrees": 1}
    kinds = {(a["symbol"], a["kind"]) for a in s["attention"]}
    assert kinds == {("A", "near-stop"), ("A", "disagree"), ("B", "stop"), ("C", "target"), ("D", "error")}


def test_live_profile_includes_summary(client):
    client.put("/api/profile/settings", json={"feePct": 0})
    client.put("/api/profile/entries", json={"symbol": "EGX30", "position": "long", "qty": 10, "avgCost": 100})
    s = client.get("/api/profile").json()["summary"]
    assert s["byCurrency"][0]["currency"] == "EGP" and s["byCurrency"][0]["pl"] == 100.0


def test_settings_sizing_and_risk(client):
    # no capital yet: cards explain instead of sizing
    client.put("/api/profile/entries", json={"symbol": "TMGH", "position": "long", "qty": 1000, "avgCost": 100, "stop": 95})
    e = client.get("/api/profile").json()["entries"][0]
    assert e["sizing"]["configured"] is False and e["exposure"] is None

    assert client.put("/api/profile/settings", json={"capital": 200000, "riskPct": 1, "maxPositionPct": 25, "feePct": 0}).status_code == 200
    assert client.put("/api/profile/settings", json={"capital": 1000, "riskPct": 50}).status_code == 422
    body = client.get("/api/profile").json()
    e = body["entries"][0]
    # last 110, my stop 95: 15/share at risk; 1% of 200k = 2000 -> 133 shares; 25% cap = 454 shares
    assert (e["sizing"]["shares"], e["sizing"]["limitedBy"], e["sizing"]["stopSource"]) == (133, "risk", "yours")
    # holding 1000 @ 110 = 110k = 55% of capital; stop at 95 -> 15k = 7.5% at risk
    assert (e["exposure"]["weightPct"], e["exposure"]["riskPct"]) == (55.0, 7.5)
    r = body["summary"]["risk"]
    # cash is what's left of the capital after paying 1000 x 100 for the holding, not after its current value
    assert (r["invested"], r["cash"], r["atRiskPct"]) == (110000.0, 100000.0, 7.5)
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


RULES = profile.Settings(capital=100_000, riskPct=1, maxPositionPct=25, feePct=0)


def act(last: float, score: float = 0.0, chart_stop: float | None = None, **entry) -> dict:
    row = live_row("X", last, score) | {"chartStop": chart_stop}
    return profile.compare(profile.Entry(symbol="X", **entry), row, settings=RULES)["action"]


@pytest.mark.parametrize("entry, last, action, group, reason_has", [
    # holding
    (dict(position="long", qty=100, avgCost=50, stop=45, target=70), 44.0, "Cut loss", "Sell", "broke your stop"),
    (dict(position="long", qty=100, avgCost=40, stop=45, target=70), 44.0, "Exit (stop)", "Sell", "keep the gain"),
    (dict(position="long", qty=100, avgCost=50, stop=45, target=70), 71.0, "Take profit", "Sell", "reached your target"),
    (dict(position="long", qty=100, avgCost=50, stop=45, expectation="bearish"), 50.0, "Sell", "Sell", "expect it to fall"),
    # 1000 x 50 = 50% of capital, limit 25% -> keep 500, sell 500
    (dict(position="long", qty=1000, avgCost=40, stop=49, expectation="bullish"), 50.0, "Trim", "Sell", "Sell 500"),
    (dict(position="long", qty=100, avgCost=50, expectation="bullish"), 50.0, "Set stop", "Hold", "no stop-loss"),
    (dict(position="long", qty=100, avgCost=50, stop=49, expectation="bullish"), 50.0, "Watch closely", "Hold", "above your stop"),
    # rules allow 1000/(55-45)=100 shares and I hold 100: no room -> Hold
    (dict(position="long", qty=100, avgCost=50, stop=45, target=70, expectation="bullish"), 55.0, "Hold", "Hold", "stay in"),
    # rules allow 100, I hold 40 -> add 60; target gain 15 >= stop risk 10
    (dict(position="long", qty=40, avgCost=50, stop=45, target=70, expectation="bullish"), 55.0, "Add more", "Buy", "60 more shares"),
    # watching
    (dict(expectation="neutral"), 50.0, "Watch", "Wait", "isn't bullish"),
    (dict(expectation="bullish", stop=45, target=52), 50.0, "Wait for dip", "Wait", "gains less"),
    (dict(expectation="bullish", target=48), 50.0, "Wait", "Wait", "already at your target"),
    # 1% of 100k = 1000 / 5 per share = 200 shares (10k, under the 25% cap)
    (dict(expectation="bullish", stop=45, target=70), 50.0, "Buy", "Buy", "200 shares"),
])
def test_actions(entry, last, action, group, reason_has):
    a = act(last, **entry)
    assert (a["action"], a["group"]) == (action, group) and reason_has in a["reason"]


def test_action_ignores_the_chart_bias():
    plan = dict(position="long", qty=100, avgCost=50, stop=45, target=70, expectation="bullish")
    assert act(55.0, score=-2.0, **plan)["action"] == act(55.0, score=2.0, **plan)["action"] == "Hold"


def test_watching_buy_uses_chart_stop_when_i_have_none():
    a = act(50.0, chart_stop=48.0, expectation="bullish")
    assert a["action"] == "Buy" and a["shares"] == 500  # 1000 / 2 = 500 shares = 25k, exactly the cap


def test_summary_groups_actions():
    rows = [profile.compare(profile.Entry(symbol=s, **kw), live_row(s, 50.0, 0.0), settings=RULES) for s, kw in [
        ("A", dict(expectation="bullish", stop=45)),
        ("B", dict(position="long", qty=10, avgCost=40, stop=45)),
        ("C", dict(position="long", qty=10, avgCost=60, stop=55)),
        ("D", dict(expectation="neutral")),
    ]]
    assert profile.summarize(rows, RULES)["todo"] == {"buy": ["A"], "hold": ["B"], "sell": ["C"], "wait": ["D"]}


def ticket(last: float, chart_stop: float | None = None, settings=RULES, **entry) -> dict | None:
    row = live_row("COMI.CA", last, 0.0) | {"chartStop": chart_stop}
    return profile.compare(profile.Entry(symbol="COMI.CA", **entry), row, settings=settings)["ticket"]


def test_order_tickets():
    b = ticket(50.004, expectation="bullish", stop=45, target=70)  # Buy 200 (1% of 100k / 5)
    assert (b["side"], b["symbol"], b["qty"], b["limit"], b["stop"]) == ("BUY", "COMI", 199, 50.01, 45)
    assert "BUY 199 COMI @ 50.01 limit" in b["text"] and "stop at 45.00" in b["text"]
    s = ticket(44.0, position="long", qty=100, avgCost=50, stop=45, target=70)  # Cut loss: sell everything
    assert (s["side"], s["qty"], s["limit"], s["stop"]) == ("SELL", 100, 44.0, None)
    t = ticket(50.0, position="long", qty=1000, avgCost=40, stop=49, expectation="bullish")  # Trim 500
    assert (t["side"], t["qty"], t["stop"]) == ("SELL", 500, 49)
    assert ticket(55.0, position="long", qty=100, avgCost=50, stop=45, target=70, expectation="bullish") is None  # Hold
    assert ticket(50.0, expectation="neutral") is None  # Watch
    coarse = profile.Settings(capital=100_000, feePct=0, tickSize=0.05)
    assert ticket(50.02, settings=coarse, expectation="bullish", stop=45, target=70)["limit"] == 50.05


# --- portfolio guardrails: cash, total risk and sector limits; the buy checklist and results dates ---

GUARD = profile.Settings(capital=100_000, riskPct=1, maxPositionPct=25, feePct=0, maxTotalRiskPct=6, maxSectorPct=35)
EMPTY_BOOK = {"cash": 100_000.0, "atRisk": 0.0, "bySector": {}}


def test_buys_shrink_to_fit_cash_total_risk_and_sector():
    size = lambda **book: profile.sizing_for(GUARD, "EGP", 50, 45, book=EMPTY_BOOK | book, sector="Finance")
    assert (size()["shares"], size()["limitedBy"], size()["limitNote"]) == (200, "risk", None)  # 1% of 100k / 5
    z = size(cash=5_000.0)
    assert (z["shares"], z["limitedBy"]) == (100, "cash") and "5,000 EGP in cash" in z["limitNote"]
    z = size(atRisk=5_800.0)  # 6% = 6000 allowed in all, 200 left -> 40 shares at 5 each
    assert (z["shares"], z["limitedBy"]) == (40, "total risk")
    z = size(bySector={"Finance": 34_000.0})  # 35% = 35k per sector, 1000 left -> 20 shares
    assert (z["shares"], z["limitedBy"]) == (20, "sector") and "Finance is already 34%" in z["limitNote"]
    z = size(cash=0.0)
    assert z["ok"] is False and z["reason"].startswith("No room")


def test_no_room_turns_buy_into_wait():
    e = profile.Entry(symbol="X", expectation="bullish", stop=45, target=70)
    row = live_row("X", 50.0, 0.0)
    res = profile.compare(e, row, settings=GUARD) | {"sizing": profile.sizing_for(GUARD, "EGP", 50, 45, book=EMPTY_BOOK | {"cash": 0.0})}
    a = profile.action_for(e, res, GUARD)
    assert a["action"] == "Wait" and "No room" in a["reason"]


def test_cash_counts_realized_gains_not_market_value():
    e = profile.Entry(symbol="X", trades=[profile.Trade(date="2026-01-05", side="buy", qty=100, price=10),
                                          profile.Trade(date="2026-02-05", side="sell", qty=100, price=12),
                                          profile.Trade(date="2026-03-05", side="buy", qty=50, price=20)])
    s = profile.Settings(capital=1_000, feePct=0)
    out = profile.compare(e, live_row("X", 30.0, 0.0), settings=s)
    book = profile.book_of([out], s, lambda sym: "Finance")
    # 1000 + 200 realized - 50 x 20 still held = 200 cash; the holding is worth 50 x 30 = 1500 in Finance
    assert (book["cash"], book["invested"], book["bySector"]) == (200.0, 1500.0, {"Finance": 1500.0})
    risk = profile.summarize([out], s, book)["risk"]
    assert risk["cash"] == 200.0 and risk["sectors"] == [{"sector": "Finance", "value": 1500.0, "pct": 150.0}]


def test_light_book_prices_from_the_market_snapshot():
    prof = profile.Profile(settings=GUARD, entries=[
        profile.Entry(symbol="COMI", position="long", qty=100, avgCost=80, stop=70),
        profile.Entry(symbol="AAPL", position="long", qty=10, avgCost=150)])  # not on the EGX: left out
    book = profile.light_book(prof, [{"symbol": "COMI.CA", "close": 100.0, "sector": "Finance"}])
    assert (book["cash"], book["invested"], book["atRisk"], book["bySector"]) == (92_000.0, 10_000.0, 3_000.0, {"Finance": 10_000.0})


def test_summary_flags_total_risk_and_sector_limits():
    rows = [profile.compare(profile.Entry(symbol="A", position="long", qty=1000, avgCost=40, stop=42), live_row("A", 50.0, 0.0), settings=GUARD)]
    book = profile.book_of(rows, GUARD, lambda s: "Real Estate")
    kinds = {a["kind"]: a for a in profile.summarize(rows, GUARD, book)["attention"]}
    assert "total-risk" in kinds and "8%" in kinds["total-risk"]["text"]  # (50 - 42) x 1000 = 8% of capital
    assert kinds["sector"]["symbol"] == "Real Estate" and "50%" in kinds["sector"]["text"]


def context(fund: dict, today="2026-10-19"):
    from datetime import date
    from app import decision
    prof = profile.Profile(settings=GUARD)
    ctx = decision.Context(prof, today=date.fromisoformat(today), book=EMPTY_BOOK)
    ctx.funds = {"X.CA": {"symbol": "X.CA", "sector": "Finance", "epsGrowth": 30.0, "revenueGrowth": 25.0, "roe": 22.0,
                          "pe": 6.0, "nextEarnings": None} | fund}
    ctx.sectors = {"Finance": {"sector": "Finance", "pe": 8.0, "verdict": "favour", "label": "Favour"}}
    return ctx


def checked_action(fund: dict | None = None, **row) -> dict:
    from datetime import datetime, timezone
    ctx = context(fund or {})
    live = live_row("X.CA", 50.0, 0.0) | {"aboveSma200": True, "adtv": 5e6, "lastBar": "2026-10-18", "chartStop": 45.0} | row
    e = profile.Entry(symbol="X.CA", expectation="bullish", stop=45, target=65)
    return profile.compare(e, live, settings=GUARD, ctx=ctx)


def test_buy_needs_a_ready_checklist():
    out = checked_action()
    assert out["decision"]["verdict"] == "ready" and out["action"]["action"] == "Buy" and out["sector"] == "Finance"
    out = checked_action(aboveSma200=False)
    assert out["action"]["action"] == "Wait" and "checklist says Avoid" in out["action"]["reason"]


def test_results_due_soon_turn_buy_into_wait_and_flag_holdings():
    from datetime import datetime, timezone
    soon = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
    out = checked_action({"nextEarnings": soon})
    assert (out["nextEarnings"], out["earningsInDays"]) == ("2026-10-24", 5)
    assert out["action"]["action"] == "Wait" and "2026-10-24" in out["action"]["reason"]
    held = out | {"position": "long"}
    assert "earnings" in {a["kind"] for a in profile.summarize([held], GUARD)["attention"]}


def test_stale_price_is_flagged():
    out = checked_action(lastBar="2026-10-01")
    assert out["stale"] is True and out["action"]["action"] == "Wait"
    assert "stale" in {a["kind"] for a in profile.summarize([out], GUARD)["attention"]}
