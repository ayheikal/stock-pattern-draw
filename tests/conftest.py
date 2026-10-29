"""Every test runs offline and on its own empty profile; tests that need market data patch it in themselves."""
from __future__ import annotations

import pytest

from app import egx, rating


def _offline(*_args):
    raise OSError("offline in tests")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("PATTERN_NO_ALERTS", "1")
    monkeypatch.setattr(egx, "fetch_market", _offline)
    monkeypatch.setattr(egx, "fetch_fundamentals", _offline)
    monkeypatch.setattr(rating, "_yahoo_info", _offline)
