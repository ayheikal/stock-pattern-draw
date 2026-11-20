"""Elliott wave count of the latest swings: the labels, the wave under way, its target and where it's wrong (no network)."""
from __future__ import annotations

import numpy as np
import pytest

from app import elliott


def swing(*legs) -> np.ndarray:
    """A close series through the given prices, 20 bars a leg."""
    return np.concatenate([np.linspace(a, b, 20) for a, b in zip(legs, legs[1:])])


def count(v):
    return elliott.count(v, v)


def labels(e):
    return [(lb["label"], round(lb["price"])) for lb in e["labels"]]


def test_five_waves_up_then_a_drop_is_wave_a():
    e = count(swing(100, 150, 120, 210, 180, 240, 215))
    assert e["direction"] == "up" and e["wave"] == "A" and e["read"] == "corrective"
    assert labels(e) == [("0", 100), ("1", 150), ("2", 120), ("3", 210), ("4", 180), ("5", 240), ("A", 215)]
    assert e["labels"][-1]["open"] and not e["labels"][-2]["open"]
    assert e["target"] == pytest.approx(180) and e["invalidation"] == pytest.approx(240)   # back to wave 4; wrong above 5


def test_wave_three_under_way_targets_1618_of_wave_one():
    e = count(swing(100, 150, 120, 190))
    assert (e["direction"], e["wave"], e["read"]) == ("up", "3", "early")
    assert e["target"] == pytest.approx(120 + 1.618 * 50) and e["invalidation"] == pytest.approx(120)
    assert "wrong past 120.00" in e["text"]


def test_wave_four_entering_wave_one_is_not_an_impulse():
    e = count(swing(100, 150, 120, 210, 140, 190, 160))
    assert e is None or not {("1", 150), ("4", 140)} <= set(labels(e))     # 140 is inside wave 1 (100-150): not a wave 4
    for pts in ([(0, 100, "L"), (20, 150, "H"), (40, 120, "L"), (60, 210, "H"), (80, 140, "L"), (100, 190, "H")],):
        assert elliott._fit(pts) is None


def test_a_finished_abc_after_five_waves_up_is_done():
    e = count(swing(100, 150, 120, 210, 180, 240, 200, 225, 185, 205))
    assert (e["wave"], e["read"]) == ("new", "done")
    assert [lb["label"] for lb in e["labels"]][-4:] == ["A", "B", "C", "new"]
    assert e["invalidation"] == pytest.approx(185)                                        # below C the correction isn't over


def test_a_falling_impulse_reads_bearish_until_its_last_wave():
    assert count(swing(240, 190, 215, 130))["read"] == "bearish"                           # wave 3 of a fall
    assert count(swing(240, 190, 215, 130, 160, 110))["read"] == "late"                    # wave 5: a bottom is near


def test_price_past_the_invalidation_breaks_the_count():
    assert elliott._broken(1, "3", 190.0, 115.0, 120.0)                                   # wave 3 fell back below wave 2's low
    assert not elliott._broken(1, "3", 190.0, 130.0, 120.0)
    assert elliott._broken(1, "A", 215.0, 245.0, 240.0)                                   # a correction can't pass the top of 5
    assert elliott._broken(-1, "3", 120.0, 195.0, 190.0)                                  # mirrored for a fall
    assert count(swing(100, 150, 120, 210, 180, 240, 215))["read"] != "broken"


@pytest.mark.parametrize("v", [np.full(200, 100.0), np.linspace(100, 101, 20)])
def test_no_count_on_flat_or_short_data(v):
    assert count(v) is None


def test_labels_count_from_the_start_of_the_arrays():
    v = np.concatenate([np.full(400, 100.0), swing(100, 150, 120, 190)])
    e = count(v)
    assert e["labels"][0]["idx"] >= 400 and v[e["labels"][-1]["idx"]] == pytest.approx(190)
