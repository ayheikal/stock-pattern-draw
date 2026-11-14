"""Pick a trade: the gate, a buy call as a trade plan, the outcome line, and the endpoint (no network)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import final, intraday, main, routine

PASS = [{"key": "trend", "label": "Above its 200-day average", "status": "pass", "detail": "The long-term trend is up."},
        {"key": "sector", "label": "Sector outlook", "status": "pass", "detail": "Consumer Durables: Accumulate."}]
HALAL = {"status": "halal", "source": "list"}


def call(symbol: str = "EGX:AAA", rr: float = 3.0, shares: int = 100, checks: list | None = None, evidence: list | None = None,
         halal: dict | None = None) -> dict:
    """A final.buy_list row: entry 10, stop 9."""
    sizing = ({"ok": True, "shares": shares, "cost": shares * 10.0, "risk": shares * 1.0, "riskPct": 1.5, "limitNote": None} if shares
              else {"ok": False, "configured": False, "reason": "Set your capital in My profile to see position sizes."})
    return {"symbol": symbol, "name": symbol.title(), "currency": "EGP", "last": 10.0, "changePct": 1.0, "position": None,
            "profileSymbol": None, "decision": {"key": "buy", "label": "Buy", "tone": "bullish", "group": "Buy", "reason": "The buy checklist passes."},
            "levels": {"entry": 10.0, "stop": 9.0, "target": 10 + rr, "rr": rr, "upsidePct": rr * 10, "source": "chart"},
            "sizing": sizing, "ticket": None, "adtv": 1e7, "lastBar": "2026-11-12", "halal": halal,
            "evidence": evidence if evidence is not None else [
                {"key": "rating", "source": "Rating", "reading": "Strong buy", "stance": "for", "role": "weighs", "detail": "Strong business."},
                {"key": "value", "source": "Fair value", "reading": "Above fair value", "stance": "against", "role": "weighs", "detail": "74% above."}],
            "checks": PASS if checks is None else checks}


def buys(results: list, mood: str | None = "neutral", watch: list | None = None) -> dict:
    return {"checked": 77, "skipped": [], "counts": {"dont-buy": 40, "wait": 20, "buy": len(results)}, "results": results,
            "mood": mood and {"key": mood, "text": "EGX 30 mixed."}, "watch": watch or [], "asOf": 1794616298}


@pytest.fixture
def client():
    return TestClient(main.app)


def test_only_a_weak_market_closes_the_gate():
    assert [routine.gate({"key": k, "text": "x"})["go"] for k in ("strong", "neutral", "weak", "unknown")] == [True, True, False, True]
    assert routine.gate({"key": "weak", "text": "x"})["tone"] == "bearish" and routine.gate({"key": "strong", "text": "x"})["tone"] == "bullish"
    g = routine.gate(None)                                    # a US list: the mood is only measured for the EGX
    assert g["go"] and "EGX only" in g["text"]


def test_a_buy_call_becomes_a_plan_with_what_speaks_for_and_against_it():
    warn = {"key": "fundamentals", "label": "Fundamental score", "status": "warn", "detail": "50%: average."}
    p = routine.plan(call(checks=PASS + [warn]))
    assert (p["shares"], p["entry"], p["stop"], p["target"], p["rr"], p["stopPct"]) == (100, 10.0, 9.0, 13.0, 3.0, -10.0)
    assert (p["cost"], p["risk"], p["riskPct"]) == (1000.0, 100.0, 1.5)
    assert p["for"] == ["Rating: Strong buy", "Above its 200-day average", "Sector outlook"]
    assert [a["label"] for a in p["against"]] == ["Fair value: Above fair value", "Fundamental score"]
    assert p["clean"] and p["sectorOk"] and p["flags"] == [] and p["timing"] is None


def test_shaky_plans_are_flagged():
    flags = lambda **kw: [f["key"] for f in routine.plan(call(**kw))["flags"]]
    assert flags(symbol="HDBK.CA") == ["source"]              # prices from the fallback source: the call can be wrong
    assert flags(rr=1.4) == ["rr"]
    assert flags(shares=0) == ["size"] and routine.plan(call(shares=0))["shares"] is None and routine.plan(call(shares=0))["risk"] is None
    blind = [{"key": k, "label": k, "status": "unknown", "detail": ""} for k in ("quality", "valuation", "earnings")]
    p = routine.plan(call(checks=PASS + blind))
    assert [f["key"] for f in p["flags"]] == ["data"] and p["noData"] == ["quality", "valuation", "earnings"] and p["clean"]   # thin data alone isn't a blocker
    assert not routine.plan(call(rr=1.4))["clean"] and not routine.plan(call(symbol="HDBK.CA"))["clean"]


def test_timing_is_a_reasonable_moment_from_four_rules_up():
    row = lambda n: {"passed": n, "lastBar": "2026-11-12 14:15", "vwapPct": 0.5, "rvol": 2.0,
                     "checks": [{"key": "trend", "label": "Trend", "ok": True, "text": "EMA 8 above EMA 20"}]}
    assert routine.timing_view(row(4))["ok"] and not routine.timing_view(row(3))["ok"]
    assert "Wait" in routine.timing_view(row(3))["advice"]
    assert routine.timing_view({"symbol": "X", "error": "no data"}) is None and routine.timing_view(None) is None


def test_outcome_says_trade_no_trade_or_weak_market():
    two = [call("EGX:SHAKY", rr=1.2), call("EGX:GOOD")]
    out = routine.build(buys(two), timings={"EGX:GOOD": {"passed": 5, "lastBar": "b", "checks": []}})
    assert out["outcome"]["key"] == "trade" and out["outcome"]["title"] == "2 stocks to consider: EGX:GOOD, EGX:SHAKY"
    assert out["outcome"]["text"] == "Cleanest: EGX:GOOD." and [p["symbol"] for p in out["plans"]] == ["EGX:GOOD", "EGX:SHAKY"]   # clean first
    assert out["plans"][0]["timing"]["ok"] and out["plans"][1]["timing"] is None
    assert out["shortlist"] == {"checked": 77, "counts": {"dont-buy": 40, "wait": 20, "buy": 2}, "skipped": [], "buys": 2, "shown": 2}

    none = routine.build(buys([]))
    assert none["outcome"]["key"] == "none" and none["outcome"]["title"] == "No trade today" and none["plans"] == []

    weak = routine.build(buys(two, mood="weak"))
    assert weak["outcome"]["key"] == "weak" and "would otherwise qualify" in weak["outcome"]["text"] and len(weak["plans"]) == 2
    assert not weak["market"]["go"]


def test_a_favoured_sector_ranks_first_and_only_the_top_five_are_planned():
    plain = [c for c in PASS if c["key"] != "sector"]
    rows = [call(f"EGX:N{i}", checks=plain) for i in range(6)] + [call("EGX:FAV")]
    out = routine.build(buys(rows[:4] + [rows[6]] + rows[4:6]))
    assert [p["symbol"] for p in out["plans"]] == ["EGX:FAV", "EGX:N0", "EGX:N1", "EGX:N2", "EGX:N3"] and out["shortlist"]["shown"] == 5


def test_halal_only_filters_plans_and_the_watch_list():
    watch = [{"symbol": "EGX:W1", "name": "W1", "last": 1.0, "changePct": 0.0, "passed": 9, "known": 11, "reason": "Sector outlook.", "halal": HALAL},
             {"symbol": "EGX:W2", "name": "W2", "last": 1.0, "changePct": 0.0, "passed": 8, "known": 11, "reason": "Results due.", "halal": None}]
    b = buys([call("EGX:YES", halal=HALAL), call("EGX:NO")], watch=watch)
    out = routine.build(b, halal_only=True)
    assert [p["symbol"] for p in out["plans"]] == ["EGX:YES"] and [w["symbol"] for w in out["watch"]] == ["EGX:W1"]
    assert len(routine.build(b)["plans"]) == 2 and len(routine.build(b)["watch"]) == 2
    only_other = routine.build(buys([call("EGX:NO")]), halal_only=True)
    assert only_other["outcome"]["key"] == "none" and "1 left out by Halal only" in only_other["outcome"]["text"]


def test_sector_view_splits_the_ones_to_prefer_from_the_ones_to_be_wary_of():
    s = lambda name, v: {"sector": name, "slug": name.lower(), "conclusion": {"verdict": v, "label": v.title()}}
    view = routine.sector_view([s("Minerals", "accumulate"), s("Finance", "neutral"), s("Retail", "caution"), s("Food", "avoid"), s("Tech", "favour")])
    assert [x["sector"] for x in view["prefer"]] == ["Minerals", "Tech"] and [x["sector"] for x in view["wary"]] == ["Retail", "Food"]
    assert routine.sector_view(None) is None and routine.build(buys([]))["sectors"] is None


def test_buy_list_also_returns_the_checks_and_the_stocks_waiting(monkeypatch):
    from app import screener
    live = lambda s, adtv: {"symbol": s, "name": s, "currency": "EGP", "last": 1.0, "changePct": 0.0, "adtv": adtv}
    monkeypatch.setattr(screener, "scan", lambda syms, i, p: {"skipped": [], "results": [live("B.CA", 1e6), live("W1.CA", 1e6), live("W2.CA", 5e6), live("W3.CA", 1e6)]})

    def call_for(row, shared, interval):
        key = "buy" if row["symbol"] == "B.CA" else "wait"
        n = {"B.CA": 3, "W1.CA": 1, "W2.CA": 2, "W3.CA": 2}[row["symbol"]]
        checks = [{"key": f"k{i}", "label": f"k{i}", "status": "pass" if i < n else "unknown", "detail": ""} for i in range(3)]
        return row | {"position": None, "profileSymbol": None, "levels": None, "sizing": None, "ticket": None, "lastBar": "2026-11-12",
                      "decision": {"key": key, "label": key, "group": "Buy" if key == "buy" else "Wait", "reason": f"why {row['symbol']}"},
                      "evidence": [{"key": "rating", "source": "Rating", "reading": "Buy", "stance": "for", "role": "weighs", "detail": "d"}],
                      "checks": checks}

    monkeypatch.setattr(final, "call_for", call_for)
    out = final.buy_list(["B.CA", "W1.CA", "W2.CA", "W3.CA"])
    assert [r["symbol"] for r in out["results"]] == ["B.CA"] and len(out["results"][0]["checks"]) == 3
    assert out["results"][0]["lastBar"] == "2026-11-12" and out["results"][0]["evidence"][0]["detail"] == "d"
    # most checks passed first, then the more liquid
    assert [(w["symbol"], w["passed"], w["known"], w["reason"]) for w in out["watch"]] == [
        ("W2.CA", 2, 2, "why W2.CA"), ("W3.CA", 2, 2, "why W3.CA"), ("W1.CA", 1, 1, "why W1.CA")]


def test_routine_endpoint_times_each_plan_and_survives_without_sectors(client, monkeypatch):
    asked = []
    monkeypatch.setattr(main, "api_final_buys", lambda u, i, refresh: asked.append((u, i, refresh)) or buys([call("EGX:AAA", halal=HALAL), call("EGX:BBB")]))

    def no_sectors(prof):
        raise HTTPException(502, "offline")

    monkeypatch.setattr(main, "_sectors_payload", no_sectors)
    monkeypatch.setattr(intraday, "_scan_one", lambda s, bars: {"symbol": s, "error": "no data"} if s == "EGX:BBB" else
                        {"symbol": s, "passed": 4, "lastBar": "2026-11-12 14:15", "barTime": "2026-11-12T14:15:00+02:00", "vwapPct": 0.4, "rvol": 2.0, "checks": []})
    r = client.get("/api/routine?universe=egx&bars=5m&refresh=true").json()
    assert asked == [("egx", "1d", True)] and (r["universe"], r["bars"]) == ("egx", "5m") and r["sectors"] is None
    assert [(p["symbol"], p["timing"] and p["timing"]["passed"]) for p in r["plans"]] == [("EGX:AAA", 4), ("EGX:BBB", None)]
    assert r["outcome"]["key"] == "trade" and r["note"]
    assert [p["symbol"] for p in client.get("/api/routine?halal_only=true").json()["plans"]] == ["EGX:AAA"]
    assert client.get("/api/routine?bars=1d").status_code == 400
    assert client.get("/pick").status_code == 200
