"""Reversal patterns: head & shoulders, double and triple tops/bottoms."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pivots import Pivot
from .common import first_break, line_through, project, segment, target_segment
from .models import Pattern


def _flip(pivots: list[Pivot]) -> list[Pivot]:
    """Mirror prices so bottom patterns can reuse the top-pattern logic."""
    return [Pivot(p.idx, -p.price, "L" if p.kind == "H" else "H") for p in pivots]


def _is_prior_extreme(ext: np.ndarray, first: int, peak: float, lookback: int, tol: float) -> bool:
    """A top must be the highest point of the move leading into it (mirrored for bottoms)."""
    lo = max(0, first - lookback)
    return first == lo or float(ext[lo:first].max()) <= peak + tol


def _head_shoulders(piv: list[Pivot], close: np.ndarray, ext: np.ndarray, atr: np.ndarray, top: bool) -> list[Pattern]:
    n = len(close)
    sgn = 1 if top else -1
    c = close * sgn
    out = []
    for k in range(len(piv) - 4):
        ls, t1, hd, t2, rs = piv[k : k + 5]
        if ls.kind != "H":
            continue
        a = atr[hd.idx]
        neck_m, neck_b = line_through((t1.idx, t1.price), (t2.idx, t2.price))
        neck_at_head = neck_m * hd.idx + neck_b
        height = hd.price - neck_at_head
        shoulder_h = min(ls.price, rs.price) - max(t1.price, t2.price)
        if hd.price <= max(ls.price, rs.price) + 0.3 * a or height < 2.5 * a or shoulder_h < 0.8 * a:
            continue
        # Shoulders of similar height, head clearly dominant.
        sym_price = abs(ls.price - rs.price) / height
        if sym_price > 0.45 or (hd.price - max(ls.price, rs.price)) / height < 0.15:
            continue
        # Roughly symmetric in time and a neckline that is not too steep.
        left_w, right_w = hd.idx - ls.idx, rs.idx - hd.idx
        sym_time = abs(left_w - right_w) / max(left_w, right_w)
        if sym_time > 0.6 or abs(neck_m) * (rs.idx - ls.idx) > 0.6 * height:
            continue
        # Prior trend: price should approach the left shoulder from below.
        if k > 0 and piv[k - 1].price > t1.price:
            continue
        if not _is_prior_extreme(ext, ls.idx, hd.price, rs.idx - ls.idx, 0.25 * a):
            continue

        horizon = rs.idx + 2 * (rs.idx - hd.idx) + 5
        brk = first_break(c, rs.idx, horizon, neck_m, neck_b, below=True)
        if brk is None and horizon < n:
            continue  # neckline never broke: the pattern failed
        end = brk if brk is not None else rs.idx
        start = ls.idx - max(3, (t1.idx - ls.idx) // 2)
        neck_price_end = neck_m * end + neck_b
        target = project(neck_price_end * sgn, height, up=not top)
        conf = 0.55 + 0.2 * (1 - sym_price / 0.45) + 0.15 * (1 - sym_time / 0.6) + (0.1 if brk is not None else 0)

        pts = [(p.idx, p.price * sgn) for p in (ls, t1, hd, t2, rs)]
        neck = segment(neck_m * sgn, neck_b * sgn, start, end + 3, n)
        out.append(
            Pattern(
                name="Head and Shoulders" if top else "Inverse Head and Shoulders",
                category="reversal",
                direction="bearish" if top else "bullish",
                start=max(0, start),
                end=end,
                confidence=round(min(conf, 0.99), 2),
                points=pts,
                lines=[pts, neck, target_segment(end, target, n)],
                target=round(target, 4),
                confirmed=brk is not None,
                label="H&S" if top else "Inv H&S",
            )
        )
    return out


def _multi_top(piv: list[Pivot], close: np.ndarray, ext: np.ndarray, atr: np.ndarray, top: bool, count: int) -> list[Pattern]:
    n = len(close)
    sgn = 1 if top else -1
    c = close * sgn
    out = []
    span = 2 * count - 1  # H L H [L H]
    for k in range(len(piv) - span + 1):
        seq = piv[k : k + span]
        if seq[0].kind != "H":
            continue
        peaks, troughs = seq[0::2], seq[1::2]
        a = float(np.mean([atr[p.idx] for p in peaks]))
        hi, lo = max(p.price for p in peaks), min(p.price for p in peaks)
        tol = max(0.8 * a, 0.015 * abs(hi))
        if hi - lo > tol:
            continue
        neck = min(t.price for t in troughs)
        depth = lo - neck
        if depth < 2 * a:
            continue
        if seq[-1].idx - seq[0].idx < 4 * count:
            continue
        # Nothing between the peaks may exceed the peak zone.
        if c[seq[0].idx : seq[-1].idx + 1].max() > hi + 0.25 * a:
            continue
        # The move into the first peak should come from below the neckline region.
        if k > 0 and piv[k - 1].price > neck + 0.5 * depth:
            continue
        if not _is_prior_extreme(ext, seq[0].idx, hi, max(20, seq[-1].idx - seq[0].idx), 0.25 * a):
            continue

        last = seq[-1].idx
        horizon = last + (last - seq[0].idx) + 5
        brk = first_break(c, last, horizon, 0.0, neck, below=True)
        if brk is None and horizon < n:
            continue  # neckline never broke: the pattern failed
        end = brk if brk is not None else last
        start = max(0, seq[0].idx - 3)
        mean_peak = float(np.mean([p.price for p in peaks]))
        target = project(neck * sgn, mean_peak - neck, up=not top)
        flatness = (hi - lo) / tol
        conf = 0.5 + 0.2 * (1 - flatness) + min(0.15, 0.05 * depth / a) + (0.12 if brk is not None else 0)

        names = {2: ("Double Top", "Double Bottom"), 3: ("Triple Top", "Triple Bottom")}[count]
        pts = [(p.idx, p.price * sgn) for p in seq]
        out.append(
            Pattern(
                name=names[0] if top else names[1],
                category="reversal",
                direction="bearish" if top else "bullish",
                start=start,
                end=end,
                confidence=round(min(conf, 0.99), 2),
                points=pts,
                lines=[pts, segment(0.0, neck * sgn, start, end + 3, n), target_segment(end, target, n)],
                target=round(target, 4),
                confirmed=brk is not None,
                label=("DT" if count == 2 else "TT") if top else ("DB" if count == 2 else "TB"),
            )
        )
    return out


def detect_reversals(df: pd.DataFrame, piv: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    close = df["Close"].to_numpy()
    high, neg_low = df["High"].to_numpy(), -df["Low"].to_numpy()
    flipped = _flip(piv)
    out: list[Pattern] = []
    out += _head_shoulders(piv, close, high, atr, top=True)
    out += _head_shoulders(flipped, close, neg_low, atr, top=False)
    for count in (3, 2):
        out += _multi_top(piv, close, high, atr, top=True, count=count)
        out += _multi_top(flipped, close, neg_low, atr, top=False, count=count)
    # A double top/bottom lying inside a triple of the same direction is the same formation.
    triples = [p for p in out if p.name.startswith("Triple")]
    return [
        p for p in out
        if not (p.name.startswith("Double") and any(
            t.direction == p.direction and t.start <= p.start and p.end <= t.end + 2 for t in triples
        ))
    ]
