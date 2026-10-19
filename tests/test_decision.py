"""Buy checklist: each check's edges, the verdict rules, and the screener's ranking (no network)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app import decision, profile, screener

TODAY = date(2026, 10, 19)  # a Monday; EGX trades Sun-Thu
SETTINGS = profile.Settings(minAdtv=1_000_000, earningsBlackoutDays=10)


def ts(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def row(**kw) -> dict:
    return {"symbol": "EGX:GOOD", "currency": "EGP", "last": 50.0, "aboveSma200": True, "adtv": 5e6,
            "lastBar": "2026-10-18", "chartStop": 45.0, "upTarget": 65.0, "bias": "Neutral", "score": 0.0} | kw


FUND = {"symbol": "GOOD.CA", "sector": "Finance", "epsGrowth": 30.0, "revenueGrowth": 25.0, "roe": 22.0, "netMargin": 15.0,
        "pe": 6.0, "nextEarnings": ts(date(2026, 12, 1))}
SECTOR = {"sector": "Finance", "pe": 8.0, "verdict": "favour", "label": "Favour"}
PLAN = {"entry": 50.0, "stop": 45.0, "target": 65.0, "source": "yours"}
SIZING = {"ok": True, "configured": True, "shares": 200, "riskPct": 1.0}


def run(r=None, fund=FUND, sector=SECTOR, halal=None, plan=PLAN, sizing=SIZING, interval="1d"):
    return decision.checklist(r or row(), fund=fund, sector=sector, halal=halal, settings=SETTINGS, inflation_pct=12,
                              plan=plan, sizing=sizing, today=TODAY, interval=interval)


def status(d: dict, key: str) -> str:
    return next(c for c in d["checks"] if c["key"] == key)["status"]


def test_a_strong_stock_is_ready():
    d = run()
    assert (d["verdict"], d["label"]) == ("ready", "Ready to buy")
    assert d["counts"]["fail"] == 0 and d["passed"] >= 9
    assert "no reliable edge" in d["chart"]["note"]  # the chart is shown, never a check
    assert d["nextEarnings"] == "2026-12-01"


def test_growth_edges():
    g = lambda **kw: decision.check_growth(FUND | kw, 12)["status"]
    assert g() == "pass"
    assert g(revenueGrowth=5.0) == "warn"                      # earnings beat inflation, revenue doesn't
    assert g(epsGrowth=-10.0, revenueGrowth=5.0) == "fail"     # earnings shrinking
    assert g(epsGrowth=None, revenueGrowth=None) == "unknown"
    assert g(epsGrowth=None, revenueGrowth=20.0) == "pass"     # judged on what's reported


def test_quality_valuation_trend_liquidity():
    q = lambda roe: decision.check_quality(FUND | {"roe": roe}, 12)["status"]
    assert (q(20.0), q(5.0), q(-3.0), q(None)) == ("pass", "warn", "fail", "unknown")
    v = lambda pe, med=8.0: decision.check_valuation(FUND | {"pe": pe}, SECTOR | {"pe": med})["status"]
    assert (v(6.0), v(12.0), v(20.0), v(None), v(6.0, None)) == ("pass", "warn", "fail", "unknown", "unknown")
    assert decision.check_trend(row(aboveSma200=False))["status"] == "fail"
    assert decision.check_trend(row(aboveSma200=None))["status"] == "unknown"
    assert decision.check_liquidity(row(adtv=200_000), 1e6)["status"] == "warn"


def test_reward_risk_edges():
    rr = lambda target, stop=45.0, source="yours": decision.check_rr(PLAN | {"target": target, "stop": stop, "source": source})["status"]
    assert (rr(60.0), rr(57.0), rr(52.0), rr(49.0)) == ("pass", "warn", "fail", "fail")  # 2:1, 1.4:1, 0.4:1, no upside
    assert rr(52.0, source="chart") == "warn"  # the chart's own levels never fail a stock
    assert rr(60.0, stop=55.0) == "unknown"  # stop above the price
    assert decision.check_rr(None)["status"] == "unknown"


def test_sector_limit_with_nothing_held_is_a_size_cap_not_a_reduction():
    settings = profile.Settings(capital=100_000, riskPct=10, maxPositionPct=50, feePct=0, maxSectorPct=35)
    book = {"cash": 100_000.0, "atRisk": 0.0, "bySector": {}}
    z = profile.sizing_for(settings, "EGP", 50, 45, book=book, sector="Finance")
    assert (z["shares"], z["limitedBy"]) == (700, "sector size")  # 35k / 50
    assert decision.check_fit(z)["status"] == "pass"
    z = profile.sizing_for(settings, "EGP", 50, 45, book=book | {"bySector": {"Finance": 20_000.0}}, sector="Finance")
    assert (z["shares"], z["limitedBy"]) == (300, "sector") and decision.check_fit(z)["status"] == "warn"


def test_plan_prefers_my_levels_over_the_chart():
    mine = profile.Entry(symbol="GOOD", stop=46, target=70)
    assert decision.plan_for(row(), mine) == {"entry": 50.0, "stop": 46, "target": 70, "source": "yours"}
    assert decision.plan_for(row())["source"] == "chart"
    assert decision.plan_for(row(), profile.Entry(symbol="GOOD", target=70))["source"] == "mixed"


def test_results_due_soon_means_wait():
    d = run(fund=FUND | {"nextEarnings": ts(date(2026, 10, 25))})
    assert d["verdict"] == "wait" and "2026-10-25" in d["reason"]


def test_stale_data_counts_egx_sessions_and_holidays():
    assert decision.sessions_missed(date(2026, 10, 15), TODAY, egx=True) == 1   # Thu -> only Sunday missed
    assert decision.sessions_missed(date(2026, 10, 15), TODAY, egx=False) == 1  # Thu -> only Friday missed
    assert decision.check_fresh(row(lastBar="2026-10-13"), TODAY, "1d")["status"] == "warn"  # Wed, Thu, Sun missed
    holidays = [date(2026, 10, 14), date(2026, 10, 15)]
    assert decision.check_fresh(row(lastBar="2026-10-13"), TODAY, "1d", holidays)["status"] == "pass"
    assert run(row(lastBar="2026-10-01"))["verdict"] == "wait"


def test_any_fail_is_avoid_and_halal_is_never_a_fail():
    d = run(row(aboveSma200=False))
    assert d["verdict"] == "avoid" and "200-day" in d["reason"]
    not_halal = run(halal={"status": "not", "source": "mine"})
    assert status(not_halal, "halal") == "warn" and not_halal["counts"]["fail"] == 0
    assert status(run(halal={"status": "halal", "source": "list"}), "halal") == "pass"


def test_missing_data_is_never_ready():
    d = run(fund=None, sector=None)  # e.g. a US stock: no EGX fundamentals
    assert d["verdict"] == "unknown" and "growth" in d["reason"]


def test_two_warnings_mean_wait_and_no_room_means_wait():
    d = run(r=row(adtv=200_000), fund=FUND | {"pe": 12.0})  # thin and pricier than its sector
    assert d["verdict"] == "wait"
    no_room = {"ok": False, "configured": True, "limitedBy": "cash", "reason": "No room for a new position: no cash."}
    assert run(sizing=no_room)["verdict"] == "wait"
    other_currency = {"ok": False, "configured": True, "reason": "Your capital is in EGP; this is priced in USD."}
    assert status(run(sizing=other_currency), "fit") == "unknown"


def test_an_index_has_no_checklist():
    d = run(row(currency=None))
    assert d["applicable"] is False and d["checks"] == []


def test_screener_ranks_by_verdict_then_checks_then_liquidity():
    def r(sym, verdict, passed, adtv):
        rank = decision.VERDICTS[verdict][1]
        return {"symbol": sym, "adtv": adtv, "decision": {"verdict": verdict, "rank": rank, "passed": passed}}
    rows = [r("AVOID", "avoid", 9, 9e9), r("WAIT", "wait", 9, 1e6), r("READY_THIN", "ready", 8, 1e6),
            r("READY_BEST", "ready", 9, 1e6), r("READY_LIQUID", "ready", 8, 5e6), r("UNKNOWN", "unknown", 2, None)]
    assert [x["symbol"] for x in screener.rank(rows)] == ["READY_BEST", "READY_LIQUID", "READY_THIN", "WAIT", "UNKNOWN"]
    assert [x["symbol"] for x in screener.rank(rows, "ready")] == ["READY_BEST", "READY_LIQUID", "READY_THIN"]
    assert screener.rank(rows, "all")[-1]["symbol"] == "AVOID"
