from app.sizing import position_risk, size_position


def test_risk_limit_decides():
    # 100k capital, 1% = 1000 budget; 50 -> 45 risks 5/share -> 200 shares = 10k (10%, under the 25% cap)
    s = size_position(100_000, 1, 25, 50, 45)
    assert (s["shares"], s["risk"], s["riskPct"], s["cost"], s["limitedBy"]) == (200, 1000, 1.0, 10_000, "risk")


def test_size_limit_caps_a_tight_stop():
    # stop only 0.5 below: risk allows 2000 shares (100k!) but 25% caps it at 500 shares
    s = size_position(100_000, 1, 25, 50, 49.5)
    assert (s["shares"], s["cost"], s["limitedBy"]) == (500, 25_000, "size")
    assert s["riskPct"] == 0.25


def test_refuses_bad_or_unaffordable_setups():
    assert "below" in size_position(100_000, 1, 25, 50, 55)["reason"]
    assert "one share" in size_position(1_000, 1, 25, 500, 400)["reason"].lower()
    assert size_position(1_000, 1, 25, 500, 499)["ok"] is False  # one share = 50% > 25% cap


def test_position_risk():
    assert position_risk(100_000, "long", 100, 50, 45) == {"value": 5000, "weightPct": 5.0, "risk": 500, "riskPct": 0.5}
    assert position_risk(100_000, "long", 100, 50, None)["risk"] is None
    assert position_risk(100_000, "long", 100, 50, 55)["risk"] == 0  # stop above price: already locked in
    assert position_risk(100_000, "short", 100, 50, 55)["risk"] == 500
