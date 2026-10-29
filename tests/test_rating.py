"""Fundamental + technical rating: each scorecard's edges, the combined signal, Yahoo's mapping and the hooks into
the buy checklist and My profile (no network)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app import decision, profile, rating

STRONG = {"symbol": "GOOD.CA", "sector": "Process Industries", "industry": "Chemicals: Specialty",
          "pe": 8.0, "pb": 1.2, "evEbitda": 6.0, "fcfYield": 9.0, "roe": 30.0, "roic": 28.0, "opMargin": 30.0, "netMargin": 20.0,
          "revenueGrowth": 35.0, "epsGrowth": 40.0, "debtToEquity": 0.2, "netDebt": -5e8, "ebitda": 2e9,
          "currentRatio": 2.0, "quickRatio": 1.4, "ocf": 1.5e9, "netIncome": 1.2e9, "fcfMargin": 15.0, "payout": 40.0}
WEAK = STRONG | {"pe": 40.0, "pb": 6.0, "evEbitda": 25.0, "fcfYield": -2.0, "roe": 4.0, "roic": 3.0, "opMargin": 3.0, "netMargin": 1.0,
                 "revenueGrowth": -5.0, "epsGrowth": -20.0, "debtToEquity": 2.5, "netDebt": 9e9, "ebitda": 1e9,
                 "currentRatio": 0.7, "quickRatio": 0.4, "ocf": 0.5e9, "fcfMargin": -4.0, "payout": 120.0}
INFLATION = 12.0


def item(card: dict, key: str) -> dict:
    return next(i for c in card["categories"] for i in c["items"] if i["key"] == key)


def bars(closes) -> pd.DataFrame:
    c = pd.Series(closes, dtype=float, index=pd.bdate_range("2024-01-01", periods=len(closes)))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e5})


# --- fundamentals ---

def test_strong_and_weak_companies_grade_as_such():
    good, bad = rating.fundamental_score(STRONG, None, INFLATION), rating.fundamental_score(WEAK, None, INFLATION)
    assert (good["grade"], good["label"]) == ("strong", "Strong") and good["pct"] >= 90 and not good["flags"]
    assert bad["grade"] == "weak" and bad["pct"] < 20
    titles = {f["title"] for f in bad["flags"]}
    assert {"Shrinking revenue", "Weak cash conversion", "Heavy debt", "Tight liquidity", "Dividend exceeds earnings"} <= titles


def test_too_few_figures_is_unknown_and_unreported_items_dont_count():
    sparse = {"pe": 8.0, "roe": 30.0, "revenueGrowth": 20.0}
    card = rating.fundamental_score(sparse, None, INFLATION)
    assert card["grade"] == "unknown" and card["pct"] is None and card["scored"] == 3
    assert item(card, "pb")["status"] == "na"


def test_valuation_and_margins_use_the_sector_median_when_there_is_one():
    med = {"pe": 10.0, "pb": 2.0, "evEbitda": 8.0, "opMargin": 20.0, "netMargin": 10.0}
    card = rating.fundamental_score(STRONG | {"pe": 12.0, "netMargin": 6.0}, med, INFLATION)
    assert item(card, "pe")["status"] == "ok" and "sector median 10" in item(card, "pe")["rule"]  # 12 is within 1.5x of 10
    assert item(card, "netMargin")["status"] == "ok"                                              # half the median or more
    assert item(rating.fundamental_score(STRONG | {"pe": 16.0}, med, INFLATION), "pe")["status"] == "weak"
    assert item(rating.fundamental_score(STRONG | {"pe": -3.0}, med, INFLATION), "pe")["status"] == "weak"


def test_growth_returns_and_peg_are_judged_after_inflation():
    card = lambda **kw: rating.fundamental_score(STRONG | kw, None, INFLATION)
    assert item(card(revenueGrowth=20.0), "revenueGrowth")["status"] == "strong"   # 12 + 5 or more
    assert item(card(revenueGrowth=14.0), "revenueGrowth")["status"] == "ok"       # beats inflation
    assert item(card(revenueGrowth=10.0), "revenueGrowth")["status"] == "weak"     # nominal growth, real decline
    assert item(card(roe=13.0), "roe")["status"] == "ok"
    peg = item(card(pe=14.0, epsGrowth=26.0), "peg")                                 # 14 / (26 - 12) = 1.0
    assert peg["value"] == 1.0 and peg["status"] == "strong"
    assert item(card(epsGrowth=10.0), "peg")["status"] == "na"                      # no real growth to pay for


def test_lenders_skip_debt_liquidity_and_cash_flow():
    bank = WEAK | {"industry": "Regional Banks", "pe": 5.0, "pb": 0.9, "roe": 25.0, "netMargin": 30.0,
                   "revenueGrowth": 30.0, "epsGrowth": 30.0}
    card = rating.fundamental_score(bank, None, INFLATION)
    assert card["financial"] is True
    for key in ("debtToEquity", "currentRatio", "cashConversion", "evEbitda", "roic"):
        assert item(card, key)["status"] == "na"
    assert card["grade"] in ("strong", "fair") and "Heavy debt" not in {f["title"] for f in card["flags"]}
    assert rating.is_financial({"source": "yahoo", "sector": "Financial Services"})
    assert not rating.is_financial({"industry": "Real Estate Development"})  # in TradingView's Finance sector, but not a lender


def test_net_cash_is_strong_and_dividend_yield_is_never_scored():
    card = rating.fundamental_score(STRONG | {"netDebt": -1e9, "ebitda": None, "dividendYield": 5.0}, None, INFLATION)
    assert item(card, "netDebtEbitda")["status"] == "strong"
    assert item(card, "dividendYield")["status"] == "info"


def test_sector_medians_need_three_companies_and_ignore_losses():
    funds = [{"sector": "A", "pe": x} for x in (5.0, 10.0, 20.0, -4.0)] + [{"sector": "B", "pe": 9.0}]
    med = rating.sector_medians(funds)
    assert med["A"]["pe"] == 10.0 and med["B"]["pe"] is None


# --- technicals ---

def test_a_steady_uptrend_scores_up_and_a_downtrend_down():
    up = rating.technical_score(bars(100 * 1.003 ** np.arange(300) + 2 * np.sin(np.arange(300))), "1d")
    down = rating.technical_score(bars(100 * 0.997 ** np.arange(300) + 2 * np.sin(np.arange(300))), "1d")
    assert (up["trend"], up["label"]) == ("up", "Uptrend") and up["pct"] >= 65
    assert down["trend"] == "down" and down["pct"] <= 35
    assert {i["key"] for i in up["items"]} == {"aboveLong", "aboveShort", "cross", "slope", "momentum", "rsi", "nearHigh"}


def test_short_history_leaves_long_checks_unscored():
    t = rating.technical_score(bars(np.linspace(100, 120, 80)), "1d")
    status = {i["key"]: i["status"] for i in t["items"]}
    assert status["aboveLong"] == status["cross"] == status["momentum"] == status["nearHigh"] == "na"
    assert t["scored"] == 2 and t["trend"] == "unknown"   # only the 50-day average and RSI: fewer than MIN_TECH_ITEMS
    assert rating.technical_score(bars(np.linspace(100, 120, 80)), "1h")["items"] == []  # only daily and weekly bars


def test_weekly_bars_use_week_lengths():
    t = rating.technical_score(bars(100 * 1.01 ** np.arange(60)), "1wk")
    assert next(i for i in t["items"] if i["key"] == "aboveLong")["label"] == "Above its 40-week average"


# --- combined signal ---

def test_signal_crosses_the_fundamental_grade_with_the_trend():
    sig = lambda f, t: rating.combine({"grade": f, "pct": 60, "flags": []}, {"trend": t, "pct": 60, "rsi": 55})
    assert sig("strong", "up")["key"] == "strong-buy"
    assert sig("weak", "down")["key"] == "sell" and sig("weak", "down")["tone"] == "bearish"
    assert sig("strong", "down")["label"] == "Watch for a turn"
    assert sig("fair", "mixed")["key"] == "hold"
    assert sig("unknown", "unknown")["key"] == "unknown"
    assert rating.combine({"grade": "strong", "pct": 80, "flags": []}, None)["key"] == "hold"  # no price history
    hot = rating.combine({"grade": "strong", "pct": 80, "flags": []}, {"trend": "up", "pct": 90, "rsi": 78})
    assert hot["score"] == 85 and any("Overbought" in n for n in hot["notes"])


def test_every_combination_has_a_signal():
    for f in ("strong", "fair", "weak", "unknown"):
        for t in ("up", "mixed", "down", "unknown"):
            assert (f, t) in rating.SIGNALS


# --- Yahoo Finance ---

def test_yahoo_info_maps_to_the_same_fields():
    info = {"quoteType": "EQUITY", "symbol": "AAPL", "sector": "Technology", "trailingPE": 30.0, "priceToBook": 40.0,
            "returnOnEquity": 1.5, "operatingMargins": 0.3, "profitMargins": 0.25, "revenueGrowth": 0.08, "earningsGrowth": 0.1,
            "debtToEquity": 150.0, "currentRatio": 1.0, "totalDebt": 100.0, "totalCash": 60.0, "ebitda": 200.0,
            "freeCashflow": 50.0, "totalRevenue": 400.0, "marketCap": 1000.0, "payoutRatio": 0.15,
            "dividendRate": 1.0, "currentPrice": 200.0, "operatingCashflow": 80.0, "netIncomeToCommon": 70.0}
    f = rating.yahoo_fund(info)
    assert f["source"] == "yahoo" and f["roe"] == 150.0 and f["debtToEquity"] == 1.5 and f["netDebt"] == 40.0
    assert (f["fcfMargin"], f["fcfYield"], f["payout"], f["dividendYield"]) == (12.5, 5.0, 15.0, 0.5)
    assert rating.yahoo_fund({"quoteType": "ETF"}) is None and rating.yahoo_fund({}) is None


def test_yahoo_is_cached_and_offline_is_harmless(monkeypatch):
    calls = []
    monkeypatch.setattr(rating, "_yahoo_cache", type(rating._yahoo_cache)(maxsize=8, ttl=60))
    monkeypatch.setattr(rating, "_yahoo_info", lambda s: calls.append(s) or {"quoteType": "ETF"})
    assert rating.fetch_yahoo_fundamentals("spy") is None and rating.fetch_yahoo_fundamentals("SPY") is None
    assert calls == ["SPY"]                               # Yahoo's "no figures" is cached too
    monkeypatch.setattr(rating, "_yahoo_info", lambda s: 1 / 0)
    assert rating.fetch_yahoo_fundamentals("MSFT") is None


def test_only_plain_non_egx_symbols_go_to_yahoo():
    assert decision.yahoo_stock("AAPL") and decision.yahoo_stock("BRK-B")
    assert not any(decision.yahoo_stock(s) for s in ("COMI.CA", "EGX:COMI", "^GSPC", "EURUSD=X"))
    assert rating.inflation_for("EGP", 12) == 12 and rating.inflation_for("USD", 12) == rating.FOREIGN_INFLATION_PCT


# --- hooks: buy checklist and My profile ---

def test_fundamental_score_is_a_checklist_check():
    r = lambda fund: {"fundamental": rating.fundamental_score(fund, None, INFLATION)}
    assert decision.check_fundamentals(r(STRONG))["status"] == "pass"
    weak = decision.check_fundamentals(r(WEAK))
    assert weak["status"] == "fail" and "Weakest:" in weak["detail"]
    assert decision.check_fundamentals(r({"pe": 8.0}))["status"] == "unknown"
    assert decision.check_fundamentals(None)["status"] == "unknown"

    row = {"symbol": "EGX:GOOD", "currency": "EGP", "last": 50.0, "aboveSma200": True, "adtv": 5e6, "lastBar": "2026-10-18",
           "chartStop": 45.0, "upTarget": 65.0, "bias": "Neutral", "score": 0.0}
    rt = rating.rate(WEAK, None, INFLATION, None)
    d = decision.checklist(row, fund=WEAK, sector=None, halal=None, settings=profile.Settings(), inflation_pct=INFLATION,
                           plan=None, sizing=None, today=date(2026, 10, 19), rating=rt)
    assert d["verdict"] == "avoid" and d["rating"] is rt


def test_context_uses_yahoo_for_us_stocks_without_an_egx_sector(monkeypatch):
    monkeypatch.setattr(rating, "fetch_yahoo_fundamentals", lambda s: rating.yahoo_fund(
        {"quoteType": "EQUITY", "symbol": s, "sector": "Utilities", "trailingPE": 20.0, "returnOnEquity": 0.2}))
    ctx = decision.Context(profile.load(), book={})
    ctx.sectors = {"Utilities": {"sector": "Utilities", "pe": 9.0, "verdict": "favour", "label": "Favour"}}  # an EGX sector
    assert ctx.fund("AAPL")["source"] == "yahoo" and ctx.sector("AAPL") is None
    assert ctx.fund("^GSPC") is None
    rt = ctx.rating({"symbol": "AAPL", "currency": "USD", "tech": None})
    assert rt["source"] == "Yahoo Finance" and rt["fundamental"]["inflationPct"] == rating.FOREIGN_INFLATION_PCT
    assert ctx.rating({"symbol": "^GSPC", "currency": None}) is None


def test_profile_flags_a_holding_when_fundamentals_and_trend_are_both_weak():
    sell = {"rating": {"signal": {"key": "sell"}, "fundamental": {"pct": 30}}}
    entries = [{"symbol": "BAD.CA", "status": "open", "position": "long", "decision": sell},
               {"symbol": "WATCH.CA", "status": "open", "position": "none", "decision": sell}]
    att = [a for a in profile.summarize(entries)["attention"] if a["kind"] == "rating"]
    assert [a["symbol"] for a in att] == ["BAD.CA"] and "30%" in att[0]["text"]   # only what I hold
