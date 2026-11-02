"""Reversal patterns: head & shoulders, double and triple tops/bottoms, rounding tops/bottoms, diamonds, bump-and-run."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pivots import Pivot
from .common import bowl_curve, find_bowl, first_break, line_through, project, segment, target_segment
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
                lines=[pts, target_segment(end, target, n)],
                neckline=neck,
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
                lines=[pts, target_segment(end, target, n)],
                neckline=segment(0.0, neck * sgn, start, end + 3, n),
                target=round(target, 4),
                confirmed=brk is not None,
                label=("DT" if count == 2 else "TT") if top else ("DB" if count == 2 else "TB"),
            )
        )
    return out


ROUND_MIN_BARS, ROUND_MAX_BARS = 30, 250


def _rounding(piv: list[Pivot], high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, bottom: bool) -> list[Pattern]:
    """Rounding bottom (saucer): a slow, wide U after a decline, confirmed by a close above its left rim.

    Tops are found by passing mirrored prices and pivots. Unlike a cup, there's no handle, and price
    comes down into the left rim instead of rallying up to it.
    """
    n = len(close)
    sgn = 1 if bottom else -1
    out = []
    for p in piv:
        if p.kind != "H":
            continue
        left, hl, a = p.idx, high[p.idx], atr[p.idx]
        bowl = find_bowl(high, low, atr, left, ROUND_MIN_BARS, ROUND_MAX_BARS)
        if bowl is None:
            continue
        right, low_idx, depth, rounded, pos = bowl
        width = right - left
        if rounded < 0.5:  # smoother than a cup has to be
            continue
        if high[max(0, left - width // 2) : left].max(initial=-np.inf) < hl + a:
            continue  # no decline into it: a base, not a reversal
        horizon = right + max(5, width // 4)
        brk = first_break(close, right, horizon, 0.0, hl, below=False)
        if brk is None and horizon < n:
            continue  # never cleared the rim: the pattern failed
        end = brk if brk is not None else n - 1
        hr, lo = high[right], low[low_idx]
        target = project(hl * sgn, depth, up=bottom)
        conf = 0.5 + 0.15 * min(1.0, (rounded - 0.5) / 0.2) + 0.1 * (1 - abs(pos - 0.5) / 0.3) + (0.12 if brk is not None else 0)
        out.append(
            Pattern(
                name="Rounding Bottom" if bottom else "Rounding Top",
                category="reversal",
                direction="bullish" if bottom else "bearish",
                start=left,
                end=end,
                confidence=round(min(conf, 0.99), 2),
                points=[(left, hl * sgn), (low_idx, lo * sgn), (right, hr * sgn)],
                lines=[bowl_curve(left, low_idx, right, hl, hr, lo, sgn), target_segment(end, float(target), n)],
                neckline=[(left, hl * sgn), (end, hl * sgn)],
                target=round(float(target), 4),
                confirmed=brk is not None,
                label="RB" if bottom else "RT",
            )
        )
    return out


def _steps(prices: list[float], up: bool) -> bool:
    return all((b > a) if up else (b < a) for a, b in zip(prices, prices[1:]))


def _diamond(piv: list[Pivot], close: np.ndarray, hi: np.ndarray, lo: np.ndarray, atr: np.ndarray, top: bool) -> list[Pattern]:
    """Diamond top: after a rally the swings widen to a peak and trough near the middle, then narrow again;
    confirmed by a close below the lower right edge. Bottoms are found by passing mirrored prices and pivots."""
    n = len(close)
    sgn = 1 if top else -1
    c = close * sgn
    out = []
    for k in range(len(piv)):
        for m in range(6, 11):
            seq = piv[k : k + m]
            if len(seq) < m:
                break
            hs = [p for p in seq if p.kind == "H"]
            ls = [p for p in seq if p.kind == "L"]
            peak, trough = max(hs, key=lambda p: p.price), min(ls, key=lambda p: p.price)
            lh, rh = [p for p in hs if p.idx < peak.idx], [p for p in hs if p.idx > peak.idx]
            ll, rl = [p for p in ls if p.idx < trough.idx], [p for p in ls if p.idx > trough.idx]
            if not (lh and rh and ll and rl):
                continue
            start, last = seq[0].idx, seq[-1].idx
            span = last - start
            a = float(atr[start : last + 1].mean())
            width = peak.price - trough.price
            # The widest point sits near the middle, and both ends are much narrower.
            mid = ((peak.idx + trough.idx) / 2 - start) / span
            if abs(peak.idx - trough.idx) > 0.35 * span or not 0.25 <= mid <= 0.75 or width < 3 * a:
                continue
            if max(hs[0].price - ls[0].price, hs[-1].price - ls[-1].price) > 0.6 * width:
                continue
            # Widening into the widest point, narrowing after it.
            if not (_steps([p.price for p in lh] + [peak.price], True) and _steps([p.price for p in ll] + [trough.price], False)
                    and _steps([peak.price] + [p.price for p in rh], False) and _steps([trough.price] + [p.price for p in rl], True)):
                continue
            if hi[start : last + 1].max() > peak.price + 0.25 * a or lo[start : last + 1].min() < trough.price - 0.25 * a:
                continue
            # A top comes after a rally: price rose into it from below its low.
            if lo[max(0, start - span) : start].min(initial=np.inf) >= trough.price:
                continue
            if not _is_prior_extreme(hi, start, peak.price, span, 0.25 * a):
                continue

            um, ub = line_through((peak.idx, peak.price), (rh[-1].idx, rh[-1].price))
            lm, lb = line_through((trough.idx, trough.price), (rl[-1].idx, rl[-1].price))
            horizon = min(n, last + max(10, span // 2))
            brk, failed = None, False
            for i in range(last + 1, horizon):
                if c[i] < lm * i + lb - 0.1 * a:
                    brk = i
                    break
                if c[i] > um * i + ub + 0.1 * a:
                    failed = True  # broke out the wrong way
                    break
            if failed or (brk is None and horizon < n):
                continue
            end = brk if brk is not None else n - 1
            target = project((lm * end + lb) * sgn, width, up=not top)
            conf = 0.55 + 0.1 * min(1.0, (m - 6) / 3) + 0.15 * (1 - abs(mid - 0.5) / 0.25) + (0.12 if brk is not None else 0)
            pt = lambda p: (p.idx, p.price * sgn)
            out.append(
                Pattern(
                    name="Diamond Top" if top else "Diamond Bottom",
                    category="reversal",
                    direction="bearish" if top else "bullish",
                    start=start,
                    end=end,
                    confidence=round(min(conf, 0.99), 2),
                    points=[pt(p) for p in seq],
                    lines=[[pt(lh[0]), pt(peak)], segment(um * sgn, ub * sgn, peak.idx, end, n),
                           [pt(ll[0]), pt(trough)], segment(lm * sgn, lb * sgn, trough.idx, end, n),
                           target_segment(end, float(target), n)],
                    target=round(float(target), 4),
                    confirmed=brk is not None,
                    label="Dia" if top else "Dia B",
                )
            )
    return out


def _bump_and_run(piv: list[Pivot], close: np.ndarray, hi: np.ndarray, lo: np.ndarray, atr: np.ndarray, top: bool) -> list[Pattern]:
    """Bump-and-run top: a steady uptrend (the lead-in) that price leaves in a steep bump at least twice
    as tall as the lead-in's swings, then falls back through the lead-in trendline. Price tends to return
    to where the bump began. Bottoms are found by passing mirrored prices and pivots."""
    n = len(close)
    sgn = 1 if top else -1
    c = close * sgn
    lows = [p for p in piv if p.kind == "L"]
    out = []
    for p1, p2 in zip(lows, lows[1:]):
        if p2.idx - p1.idx < 10:
            continue
        m, b = line_through((p1.idx, p1.price), (p2.idx, p2.price))
        a = atr[p2.idx]
        if m <= 0:
            continue
        xs = np.arange(p1.idx, p2.idx + 1)
        if (lo[xs] < m * xs + b - 0.25 * a).any():
            continue  # the lead-in trendline must hold
        lead_h = float((hi[xs] - (m * xs + b)).max())
        brk = first_break(c, p2.idx + 1, n, m, b, below=True)
        seg = np.arange(p2.idx, brk if brk is not None else n)
        if len(seg) < 5:
            continue
        dist = hi[seg] - (m * seg + b)
        peak, bump_h = int(seg[np.argmax(dist)]), float(dist.max())
        if bump_h < max(2 * lead_h, 4 * a):
            continue
        if (hi[peak] - p2.price) / max(peak - p2.idx, 1) < 1.5 * m:
            continue  # the bump must climb faster than the lead-in
        if brk is None and (n - 1 - peak < 3 or c[n - 1] - (m * (n - 1) + b) > 0.5 * bump_h):
            continue  # still running, or hasn't rolled over halfway back to the line
        end = brk if brk is not None else n - 1
        conf = 0.55 + 0.15 * min(1.0, (bump_h / max(lead_h, a) - 2) / 2) + (0.12 if brk is not None else 0)
        out.append(
            Pattern(
                name="Bump-and-Run Top" if top else "Bump-and-Run Bottom",
                category="reversal",
                direction="bearish" if top else "bullish",
                start=p1.idx,
                end=end,
                confidence=round(min(conf, 0.99), 2),
                points=[(p1.idx, p1.price * sgn), (p2.idx, p2.price * sgn), (peak, hi[peak] * sgn)],
                lines=[segment(m * sgn, b * sgn, p1.idx, end, n),
                       [(p2.idx, p2.price * sgn), (peak, hi[peak] * sgn), (end, close[end])],
                       target_segment(end, p2.price * sgn, n)],
                target=round(float(p2.price * sgn), 4),
                confirmed=brk is not None,
                label="BARR" if top else "BARR B",
            )
        )
    return out


def detect_reversals(df: pd.DataFrame, piv: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    close = df["Close"].to_numpy()
    high, neg_low = df["High"].to_numpy(), -df["Low"].to_numpy()
    flipped = _flip(piv)
    out: list[Pattern] = []
    out += _rounding(piv, high, df["Low"].to_numpy(), close, atr, bottom=True)
    out += _rounding(flipped, neg_low, -high, -close, atr, bottom=False)
    low = df["Low"].to_numpy()
    for detect in (_diamond, _bump_and_run):
        out += detect(piv, close, high, low, atr, top=True)
        out += detect(flipped, close, neg_low, -high, atr, top=False)
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
