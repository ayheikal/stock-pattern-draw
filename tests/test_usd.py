"""EGX prices in US dollars: each close divided by the USD/EGP rate for its bar (no network)."""
from __future__ import annotations

import math

import pandas as pd

from app.data import in_usd, is_egx


def cairo(*stamps):
    return pd.DatetimeIndex(stamps).tz_localize("Africa/Cairo")


def london(*stamps):
    return pd.DatetimeIndex(stamps).tz_localize("Europe/London")


def test_is_egx():
    assert is_egx("EGX:EGX30") and is_egx("EGX:COMI") and is_egx("COMI.CA")
    assert not is_egx("AAPL") and not is_egx("^GSPC") and not is_egx("EGP=X")


def test_daily_uses_the_same_day_and_carries_the_rate_over_gaps():
    # EGX bars are stamped 10:00 Cairo, Yahoo's rate 00:00 London; Sunday has an EGX session but no FX bar.
    closes = pd.Series([48000.0, 49000.0, 50000.0], index=cairo("2026-10-29 10:00", "2026-11-01 10:00", "2026-11-02 10:00"))
    rate = pd.Series([48.0, 50.0, 55.0], index=london("2026-10-29", "2026-10-30", "2026-11-02"))
    out = in_usd(closes, rate, "1d")
    assert list(out["rate"]) == [48.0, 50.0, 55.0]  # Sunday takes Friday's rate
    assert list(out["usd"].round(4)) == [1000.0, 980.0, round(50000 / 55, 4)]


def test_weekly_uses_the_rate_at_the_end_of_each_week():
    closes = pd.Series([100.0, 200.0], index=cairo("2026-10-18", "2026-10-25"))
    rate = pd.Series([10.0, 20.0, 40.0], index=london("2026-10-18", "2026-10-23", "2026-10-29"))
    out = in_usd(closes, rate, "1wk")
    assert list(out["rate"]) == [20.0, 40.0]  # last day before the next bar; the latest rate for the last bar


def test_hourly_matches_on_utc_time():
    closes = pd.Series([110.0, 120.0], index=cairo("2026-11-02 10:00", "2026-11-02 11:00"))  # 08:00 and 09:00 UTC
    rate = pd.Series([50.0, 60.0], index=pd.DatetimeIndex(["2026-11-02 08:00", "2026-11-02 08:30"]).tz_localize("UTC"))
    out = in_usd(closes, rate, "1h")
    assert list(out["rate"]) == [50.0, 60.0]


def test_before_the_rate_history_is_nan():
    closes = pd.Series([100.0, 100.0], index=cairo("2003-01-05 10:00", "2026-11-02 10:00"))
    rate = pd.Series([50.0], index=london("2020-01-01"))
    out = in_usd(closes, rate, "1d")
    assert math.isnan(out["usd"].iloc[0]) and out["usd"].iloc[1] == 2.0
