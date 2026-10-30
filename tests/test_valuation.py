"""Below fair value: the fair price from peers' multiples, the caps, the verdicts and the endpoint (no network)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import egx, valuation

INFLATION = 12.0
SOUND = {"sector": "Process Industries", "industry": "Chemicals", "marketCap": 1e9, "close": 10.0, "sma200": 9.0,
         "pe": 10.0, "pb": 2.0, "evEbitda": 8.0, "ebitda": 1e8, "roe": 25.0, "roic": 22.0, "opMargin": 25.0, "netMargin": 15.0,
         "revenueGrowth": 25.0, "epsGrowth": 25.0, "debtToEquity": 0.3, "netDebt": 0.0, "currentRatio": 2.0, "quickRatio": 1.2,
         "ocf": 1.2e8, "netIncome": 1e8, "fcfMargin": 12.0, "payout": 40.0, "value": 5e6}


def peers(n: int = 3, **kw) -> list[dict]:
    return [SOUND | {"symbol": f"P{i}.CA"} | kw for i in range(n)]


def test_fair_value_is_the_median_of_the_peer_multiples():
    cheap = SOUND | {"symbol": "CHEAP.CA", "pe": 5.0, "pb": 1.0, "evEbitda": 4.0}
    by_ind, by_sec = valuation.peer_medians(peers() + [cheap])
    est = {e["key"]: e for e in valuation.estimates(cheap, by_ind, by_sec)}
    assert est["pe"]["fair"] == 20.0 and est["pe"]["peers"] == "Chemicals"     # 10 × 10 / 5
    assert est["pb"]["fair"] == 20.0
    assert est["evEbitda"]["fair"] == 14.0      # equity gains (8 − 4) × EBITDA 1e8 on a 1e9 market cap: +40%
    v = valuation.value(cheap, by_ind, by_sec, INFLATION)
    assert (v["fair"], v["discountPct"], v["upsidePct"]) == (20.0, 50.0, 100.0)


def test_industry_needs_three_peers_else_the_sector_is_used():
    stock = SOUND | {"symbol": "X.CA", "industry": "Rare", "pe": 5.0}
    by_ind, by_sec = valuation.peer_medians(peers() + [stock])
    assert next(e for e in valuation.estimates(stock, by_ind, by_sec) if e["key"] == "pe")["peers"] == "Process Industries"


def test_one_odd_figure_cant_decide_and_too_few_estimates_arent_valued():
    odd = SOUND | {"symbol": "ODD.CA", "pe": 0.5}                      # a one-off gain: P/E alone would say 20× the price
    by_ind, by_sec = valuation.peer_medians(peers() + [odd])
    assert max(e["fair"] for e in valuation.estimates(odd, by_ind, by_sec)) == 10.0 * valuation.MAX_RATIO
    lone = SOUND | {"symbol": "LONE.CA", "pb": None, "evEbitda": None}
    assert valuation.value(lone, by_ind, by_sec, INFLATION) is None
    bank = SOUND | {"symbol": "BANK.CA", "industry": "Regional Banks"}
    assert "evEbitda" not in {e["key"] for e in valuation.estimates(bank, by_ind, by_sec)}


def test_verdict_needs_a_sound_business_and_a_rising_trend():
    assert valuation.verdict(SOUND, "strong") == "buy"
    assert valuation.verdict(SOUND | {"close": 8.0}, "fair") == "watch"         # below its 200-day average
    assert valuation.verdict(SOUND | {"sma200": None}, "fair") == "watch"       # too new to tell
    assert valuation.verdict(SOUND, "unknown") == "unknown"
    assert valuation.verdict(SOUND, "weak") == "trap"
    assert valuation.verdict(SOUND | {"pe": -3.0}, "strong") == "trap"          # losing money


def test_screen_keeps_only_stocks_under_the_margin_best_first():
    funds = peers() + [SOUND | {"symbol": "DEEP.CA", "pe": 4.0, "pb": 0.8, "sma200": 12.0},
                       SOUND | {"symbol": "BUY.CA", "pe": 6.0, "pb": 1.2},
                       SOUND | {"symbol": "NEAR.CA", "pe": 9.0, "pb": 1.8}]
    out = valuation.screen(funds, INFLATION, margin=20)
    assert [r["symbol"] for r in out["results"]] == ["BUY.CA", "DEEP.CA"]     # Buy zone before Watch, despite the smaller discount
    assert out["counts"] == {"buy": 1, "watch": 1, "unknown": 0, "trap": 0} and out["valued"] == 6


def test_endpoint_lists_cheap_stocks_with_halal_tags(monkeypatch):
    from app.main import app
    funds = peers() + [SOUND | {"symbol": "BUY.CA", "name": "Buy Co", "pe": 6.0, "pb": 1.2}]
    monkeypatch.setattr(egx, "fetch_fundamentals", lambda: {"asOf": 1, "stocks": funds})
    b = TestClient(app).get("/api/value?margin=20").json()
    assert [r["symbol"] for r in b["results"]] == ["BUY.CA"] and "halal" in b["results"][0]
    assert TestClient(app).get("/api/value?margin=95").status_code == 422
