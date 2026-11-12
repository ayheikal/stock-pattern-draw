"""Track record of the outlook, each chart pattern and each indicator signal: replay them on history and measure what happened next.

At every sample bar the outlook sees only the trailing WINDOW bars up to that bar (what the
live app shows with its 1y daily / 5y weekly view), so no future data leaks into a signal.

CLI:  python -m app.backtest egx 1d
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .indicators import recent_signals
from .outlook import HORIZONS, build_outlook
from .patterns import Pattern, analyze
from .pivots import atr as compute_atr
from .screener import get_universe

WINDOW = 250
HISTORY = {"1d": "10y", "1wk": "max"}
BUCKETS = ["Bullish", "Mildly bullish", "Neutral", "Mildly bearish", "Bearish"]
DIRECTION = {"Bullish": 1, "Mildly bullish": 1, "Neutral": 0, "Mildly bearish": -1, "Bearish": -1}
MIN_RELIABLE = 30
TRADE_HORIZON = 12  # a pattern's target gets this many outlook horizons to be reached: ~3 months daily, ~1 year weekly
TRADE_WINDOW = {"1d": "about 3 months", "1wk": "about a year"}  # TRADE_HORIZON in words
# Look-back periods reported from one run: key -> months (None = everything).
RANGES = {"6mo": 6, "1y": 12, "2y": 24, "5y": 60, "all": None}
PARALLEL = True  # tests switch this off so monkeypatched data reaches the replay

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
_running: set[tuple[str, str]] = set()
_errors: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


def horizon_bars(interval: str) -> int:
    return HORIZONS[interval][0]


def plan_outcome(df: pd.DataFrame, i: int, plan: dict, max_bars: int) -> tuple[str, float] | None:
    """Walk forward from bar i: which of target / stop is touched first. Same bar counts as the stop."""
    entry, target, stop = plan["entry"], plan["target"], plan["stop"]
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    long = plan["side"] == "long"
    end = min(i + max_bars, len(df) - 1)
    for j in range(i + 1, end + 1):
        hi, lo = float(df["High"].iloc[j]), float(df["Low"].iloc[j])
        if (lo <= stop) if long else (hi >= stop):
            return "stop", -1.0
        if (hi >= target) if long else (lo <= target):
            return "target", round(plan["rr"], 3)
    move = float(df["Close"].iloc[end]) - entry
    return "timeout", round((move if long else -move) / risk, 3)


def completed(patterns: list[Pattern], n: int, bars: int) -> list[list[str]]:
    """[name, direction] of each pattern that completed within the last `bars` of n bars, once each.

    Forming patterns are left out (they'd count again at every sample), and so are trendlines and
    channels, which describe the trend rather than signal a breakout or a turn.
    """
    out: list[list[str]] = []
    for p in patterns:
        if p.category == "trend" or not p.confirmed or p.end < n - bars:
            continue
        if [p.name, p.direction] not in out:
            out.append([p.name, p.direction])
    return out


def pattern_trades(patterns: list[Pattern], w: pd.DataFrame, bars: int) -> list[dict]:
    """A trade for each pattern with a target that completed within the last `bars`: in at the last close,
    out at the pattern's target, stopped just beyond its last swing (where the pattern is proven wrong).
    Used to score whether the target or the stop came first."""
    n, entry = len(w), float(w["Close"].iloc[-1])
    a = float(compute_atr(w)[-1])
    out, seen = [], set()
    for p in patterns:
        if p.category == "trend" or not p.confirmed or p.target is None or p.end < n - bars:
            continue
        long = p.direction == "bullish"
        if p.direction == "neutral" or (p.name, p.direction) in seen or (p.target <= entry if long else p.target >= entry):
            continue  # pointing nowhere, counted already, or the target was reached before you'd have seen it
        i0 = int(p.points[-1][0]) if p.points else p.start
        swing = float(w["Low"].iloc[i0:].min()) if long else float(w["High"].iloc[i0:].max())
        # just beyond the swing, and at least half an ATR away so a tick of noise isn't a failure
        stop = min(swing - 0.25 * a, entry - 0.5 * a) if long else max(swing + 0.25 * a, entry + 0.5 * a)
        if stop <= 0:
            continue
        seen.add((p.name, p.direction))
        out.append({"name": p.name, "direction": p.direction, "side": "long" if long else "short", "entry": entry,
                    "target": float(p.target), "stop": stop, "rr": abs(p.target - entry) / abs(entry - stop)})
    return out


def sample_at(df: pd.DataFrame, i: int, interval: str) -> dict:
    """The outlook as it looked at the close of bar i, from bars i-WINDOW+1..i only."""
    w = df.iloc[max(0, i - WINDOW + 1): i + 1]
    patterns, levels = analyze(w)
    o = build_outlook(w, patterns, levels, interval)
    return {"score": o["score"], "bias": o["bias"], "last": float(w["Close"].iloc[-1]),
            "plan": next((p for p in o["plans"] if not p["conditional"]), None),
            "patterns": completed(patterns, len(w), horizon_bars(interval)),
            "trades": pattern_trades(patterns, w, horizon_bars(interval)),
            "signals": recent_signals(w, horizon_bars(interval))}


def replay(df: pd.DataFrame, interval: str, min_adtv: float = 0.0) -> list[dict]:
    """Samples along the history. With `min_adtv`, bars where the stock traded less than that
    per day (20-bar average, as known at that bar) are skipped: those trades couldn't really be done."""
    h = horizon_bars(interval)
    max_bars = 4 * h
    close = df["Close"]
    out = []
    # Step by h so forward windows don't overlap; stop where the full outcome window still fits.
    daily_value = (df["Close"] * df["Volume"]).rolling(20).mean() / (5 if interval == "1wk" else 1)
    for i in range(WINDOW - 1, len(df) - max_bars, h):
        if min_adtv and not daily_value.iloc[i] >= min_adtv:
            continue
        try:
            s = sample_at(df, i, interval)
        except Exception:  # a degenerate window shouldn't cost the whole symbol
            continue
        row = {
            "date": df.index[i].strftime("%Y-%m-%d"),
            "score": s["score"],
            "bias": s["bias"],
            "fwdRet": round((float(close.iloc[i + h]) / s["last"] - 1) * 100, 3),
            "outcome": None,
            "R": None,
            "patterns": s["patterns"],
            "signals": s["signals"],
            # each pattern's own trade: did its target or its stop come first (within TRADE_HORIZON horizons)?
            # (only once the whole window has played out, so the latest ones aren't counted as neither)
            "patternTrades": [{"name": t["name"], "direction": t["direction"], "outcome": res[0], "R": res[1]}
                              for t in s["trades"] if i + TRADE_HORIZON * h < len(df)
                              and (res := plan_outcome(df, i, t, TRADE_HORIZON * h))],
        }
        if s["plan"] is not None:
            res = plan_outcome(df, i, s["plan"], max_bars)
            if res:
                row["outcome"], row["R"] = res
                # risk as % of entry: turns a round-trip fee into R units (cost_R = 2 x fee / riskPct)
                row["riskPct"] = round(abs(s["plan"]["entry"] - s["plan"]["stop"]) / s["plan"]["entry"] * 100, 4)
        out.append(row)
    return out


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _share(xs: list, pred) -> float | None:
    return round(sum(1 for x in xs if pred(x)) / len(xs) * 100, 1) if xs else None


def summarize_samples(samples: list[dict], fee_pct: float = 0.0) -> dict:
    """Stats per bias bucket; the *Net fields subtract a buy and a sell at `fee_pct` each."""
    rets = [s["fwdRet"] for s in samples]
    baseline = _mean(rets)
    buckets = []
    for b in BUCKETS:
        ss = [s for s in samples if s["bias"] == b]
        r = [s["fwdRet"] for s in ss]
        d = DIRECTION[b]
        planned = [s for s in ss if s["outcome"]]
        avg = _mean(r)
        buckets.append({
            "bias": b,
            "n": len(ss),
            "avgRet": avg,
            "excess": round(avg - baseline, 3) if avg is not None and baseline is not None else None,
            "asExpected": _share(r, lambda x: x * d > 0) if d else None,
            "targetFirst": _share(planned, lambda s: s["outcome"] == "target"),
            "stopFirst": _share(planned, lambda s: s["outcome"] == "stop"),
            "timeout": _share(planned, lambda s: s["outcome"] == "timeout"),
            "avgR": _mean([s["R"] for s in planned]),
            "avgRNet": _mean([s["R"] - 2 * fee_pct / s["riskPct"] for s in planned if s.get("riskPct")]),
            "avgRetNet": round(avg - 2 * fee_pct, 3) if avg is not None and d else None,
            "small": len(ss) < MIN_RELIABLE,
        })
    return {"n": len(samples), "baseline": baseline, "upShare": _share(rets, lambda x: x > 0), "buckets": buckets}


PATTERN_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}


def summarize_patterns(samples: list[dict], key: str = "patterns") -> list[dict]:
    """How price moved over the horizon after each pattern (or indicator signal, key="signals"), most frequent first.

    Measured from the first sample at or after it completed (when you'd have seen it),
    against the base rate of all samples moving that way.
    """
    rets = [s["fwdRet"] for s in samples]
    baseline, up = _mean(rets), _share(rets, lambda x: x > 0)
    groups: dict[tuple[str, str], list[float]] = {}
    trades: dict[tuple[str, str], list[dict]] = {}
    for s in samples:
        for name, direction in s.get(key) or []:
            groups.setdefault((name, direction), []).append(s["fwdRet"])
        for t in (s.get("patternTrades") or []) if key == "patterns" else []:
            trades.setdefault((t["name"], t["direction"]), []).append(t)
    out = []
    for (name, direction), r in groups.items():
        d = PATTERN_SIGN[direction]
        avg = _mean(r)
        tr = trades.get((name, direction), [])
        out.append({
            # success: the pattern's own target reached before its stop (its last swing), within TRADE_HORIZON horizons
            "trades": len(tr),
            "targetFirst": _share(tr, lambda t: t["outcome"] == "target"),
            "stopFirst": _share(tr, lambda t: t["outcome"] == "stop"),
            "avgR": _mean([t["R"] for t in tr]),
            "name": name,
            "direction": direction,
            "n": len(r),
            "avgRet": avg,
            "excess": round(avg - baseline, 3) if avg is not None and baseline is not None else None,
            "asExpected": _share(r, lambda x: x * d > 0) if d else None,
            "baseRate": (up if d > 0 else round(100 - up, 1)) if d and up is not None else None,
            "small": len(r) < MIN_RELIABLE,
        })
    return sorted(out, key=lambda p: (-p["n"], p["name"]))


def _replay_symbol(symbol: str, interval: str, min_adtv: float = 0.0) -> dict:
    try:
        resolved, df, meta = fetch_ohlcv(symbol, interval, HISTORY[interval])
    except (SymbolNotFound, InsufficientHistory):
        return {"symbol": symbol, "error": "no data"}
    except Exception as e:  # one bad symbol shouldn't sink the run
        return {"symbol": symbol, "error": str(e)[:80]}
    if len(df) < WINDOW + 4 * horizon_bars(interval) + 1:
        return {"symbol": symbol, "error": f"only {len(df)} bars"}
    return {"symbol": symbol, "resolved": resolved, "name": meta.get("longName") or meta.get("shortName") or resolved,
            "from": df.index[0].strftime("%Y-%m-%d"), "to": df.index[-1].strftime("%Y-%m-%d"),
            # indices have no traded value of their own, so the liquidity filter is for stocks only
            "samples": replay(df, interval, min_adtv if meta.get("currency") else 0.0)}


def _period(ok: list[dict], since: str | None, fee_pct: float = 0.0) -> dict:
    """Summaries using only signals dated on/after `since`."""
    pick = lambda r: [s for s in r["samples"] if since is None or s["date"] >= since]
    every = [s for r in ok for s in pick(r)]
    return {
        "since": since or min((r["from"] for r in ok), default=None),
        "overall": summarize_samples(every, fee_pct),
        "patterns": summarize_patterns(every),
        "signals": summarize_patterns(every, "signals"),
        "perSymbol": {
            r["resolved"]: {"query": r["symbol"], "name": r["name"], "from": r["from"], **summarize_samples(pick(r), fee_pct)}
            for r in ok
        },
    }


def cutoff(last: str, months: int) -> str:
    """Start date of a look-back period ending at the last data bar."""
    return (pd.Timestamp(last) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")


def run(universe: str, interval: str, symbols: list[str] | None = None, fee_pct: float = 0.0, min_adtv: float = 0.0) -> dict:
    symbols = symbols or get_universe(universe)["symbols"]
    t0 = time.perf_counter()
    if PARALLEL and len(symbols) > 1:
        with ProcessPoolExecutor(max_workers=max(1, min(len(symbols), (os.cpu_count() or 2) - 1))) as pool:
            rows = list(pool.map(_replay_symbol, symbols, [interval] * len(symbols), [min_adtv] * len(symbols)))
    else:
        rows = [_replay_symbol(s, interval, min_adtv) for s in symbols]
    ok = [r for r in rows if "error" not in r]
    last = max((r["to"] for r in ok), default=None)
    periods = {k: _period(ok, cutoff(last, m) if m and last else None, fee_pct) for k, m in RANGES.items()}
    return {
        "universe": universe,
        "interval": interval,
        "horizonBars": horizon_bars(interval),
        "horizon": HORIZONS[interval][1],
        "window": WINDOW,
        "feePct": fee_pct,
        "minAdtv": min_adtv,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - t0, 1),
        "symbols": len(ok),
        "skipped": [f"{r['symbol']} ({r['error']})" for r in rows if "error" in r],
        "range": "all",
        **periods["all"],
        "ranges": {k: v for k, v in periods.items() if k != "all"},
    }


def view(result: dict, rng: str) -> dict:
    """The result as seen over one look-back period (same shape as the full result)."""
    out = {k: v for k, v in result.items() if k != "ranges"}
    out["periods"] = ["all"] + list(result.get("ranges", {}))
    if rng != "all":
        part = result.get("ranges", {}).get(rng)
        if part is None:  # cached before periods existed
            return out | {"rangeMissing": True}
        out |= part | {"range": rng}
    return out


# --- cache on disk + background runs ---

def _dir() -> Path:
    return Path(os.environ.get("BACKTEST_DIR") or DEFAULT_DIR)


def _file(universe: str, interval: str) -> Path:
    return _dir() / f"backtest_{universe}_{interval}.json"


def load(universe: str, interval: str) -> dict | None:
    p = _file(universe, interval)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(result: dict) -> None:
    p = _file(result["universe"], result["interval"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(p)


def status(universe: str, interval: str) -> dict:
    key = (universe, interval)
    with _lock:
        return {"running": key in _running, "error": _errors.get(key)}


def start(universe: str, interval: str, fee_pct: float = 0.0, min_adtv: float = 0.0) -> bool:
    """Run in a background thread; False if this universe/interval is already running."""
    key = (universe, interval)
    with _lock:
        if key in _running:
            return False
        _running.add(key)
        _errors.pop(key, None)

    def work():
        try:
            save(run(universe, interval, fee_pct=fee_pct, min_adtv=min_adtv))
        except Exception as e:
            with _lock:
                _errors[key] = str(e)[:200]
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=work, daemon=True).start()
    return True


MIN_EDGE = 3.0  # percentage points a hit rate must beat the base rate by to count as an edge


def pattern_verdict(p: dict) -> str | None:
    """useful / misleading / unclear / none / few, the same rule as significance() in static/track.js (change both together).

    An edge counts when it's MIN_EDGE+ points and twice the standard error of the base rate, so a few
    lucky signals don't. None for patterns that point neither way.
    """
    if p.get("asExpected") is None or p.get("baseRate") is None:
        return None
    if p["n"] < MIN_RELIABLE:
        return "few"
    base = p["baseRate"] / 100
    se = math.sqrt(base * (1 - base) / p["n"]) * 100
    edge = p["asExpected"] - p["baseRate"]
    if edge >= MIN_EDGE and edge >= 2 * se:
        return "useful"
    if edge <= -MIN_EDGE and edge <= -2 * se:
        return "misleading"
    return "unclear" if abs(edge) >= MIN_EDGE else "none"


def _is_egx(symbol: str) -> bool:
    return symbol.startswith("EGX:") or symbol.endswith(".CA")


def pattern_track(resolved: str, interval: str) -> dict | None:
    """Each pattern's record from the largest cached run of the same market (EGX or not) at this interval.

    Keyed "name|direction"; None when no such run has pattern stats yet (runs saved before they existed).
    """
    egx, best = _is_egx(resolved), None
    for p in _dir().glob(f"backtest_*_{interval}.json"):
        run = json.loads(p.read_text(encoding="utf-8"))
        per = run.get("perSymbol") or {}
        if not run.get("patterns") or (2 * sum(_is_egx(k) for k in per) > len(per)) != egx:
            continue
        if best is None or run["overall"]["n"] > best["overall"]["n"]:
            best = run
    if best is None:
        return None
    return {
        "label": "EGX" if egx else best["universe"],
        "universe": best["universe"],
        "symbols": best["symbols"],
        "since": best.get("since"),
        "horizon": best["horizon"],
        "stats": {f"{p['name']}|{p['direction']}": {"n": p["n"], "asExpected": p["asExpected"], "baseRate": p["baseRate"],
                                                     "verdict": pattern_verdict(p), "trades": p.get("trades", 0),
                                                     "targetFirst": p.get("targetFirst"), "stopFirst": p.get("stopFirst"),
                                                     "avgR": p.get("avgR")} for p in best["patterns"]},
        "tradeHorizon": TRADE_WINDOW.get(best.get("interval"), f"{TRADE_HORIZON} outlook periods"),
    }


RECENT = "1y"  # the look-back shown next to the all-history record


def track_for(resolved: str, interval: str) -> dict | None:
    """Cached per-symbol record from the newest run that includes this symbol, plus its last-year record
    as "recent" (an edge that held long ago but not lately is worth knowing about).

    An EGX stock matches under either code (EGX:COMI or COMI.CA): older runs resolved them via Yahoo.
    """
    keys = [resolved]
    if resolved.startswith("EGX:"):
        keys.append(resolved[4:] + ".CA")
    elif resolved.endswith(".CA"):
        keys.append("EGX:" + resolved[:-3])
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in _dir().glob(f"backtest_*_{interval}.json")]
    for run in sorted(runs, key=lambda r: r.get("generatedAt") or "", reverse=True):
        per = run.get("perSymbol", {})
        k = next((k for k in keys if k in per), None)
        if k is None:
            continue
        recent = run.get("ranges", {}).get(RECENT, {}).get("perSymbol", {}).get(k)
        return per[k] | {"recent": recent and {"range": RECENT, "since": run["ranges"][RECENT].get("since"), **recent}}
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    uni = sys.argv[1] if len(sys.argv) > 1 else "egx"
    iv = sys.argv[2] if len(sys.argv) > 2 else "1wk"
    from .profile import load as load_profile  # the CLI uses the fee saved in My profile
    rules = load_profile().settings
    res = run(uni, iv, fee_pct=rules.feePct, min_adtv=rules.minAdtv)
    save(res)
    print(f"{res['symbols']} symbols in {res['seconds']}s; skipped: {', '.join(res['skipped']) or 'none'}")
    o = res["overall"]
    print(f"baseline avg {o['baseline']}% over {res['horizon']} (n={o['n']}, up {o['upShare']}%)")
    for b in o["buckets"]:
        print(f"  {b['bias']:<15} n={b['n']:<5} avg={b['avgRet']}%  excess={b['excess']}  as-expected={b['asExpected']}%  "
              f"target-first={b['targetFirst']}%  stop-first={b['stopFirst']}%  avgR={b['avgR']}")
