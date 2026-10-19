"""Track record of the outlook: replay it on history and measure what happened next.

At every sample bar the outlook sees only the trailing WINDOW bars up to that bar (what the
live app shows with its 1y daily / 5y weekly view), so no future data leaks into a signal.

CLI:  python -m app.backtest egx 1d
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .outlook import HORIZONS, build_outlook
from .patterns import analyze
from .screener import get_universe

WINDOW = 250
HISTORY = {"1d": "10y", "1wk": "max"}
BUCKETS = ["Bullish", "Mildly bullish", "Neutral", "Mildly bearish", "Bearish"]
DIRECTION = {"Bullish": 1, "Mildly bullish": 1, "Neutral": 0, "Mildly bearish": -1, "Bearish": -1}
MIN_RELIABLE = 30
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


def sample_at(df: pd.DataFrame, i: int, interval: str) -> dict:
    """The outlook as it looked at the close of bar i, from bars i-WINDOW+1..i only."""
    w = df.iloc[max(0, i - WINDOW + 1): i + 1]
    patterns, levels = analyze(w)
    o = build_outlook(w, patterns, levels, interval)
    return {"score": o["score"], "bias": o["bias"], "last": float(w["Close"].iloc[-1]),
            "plan": next((p for p in o["plans"] if not p["conditional"]), None)}


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
    return {
        "since": since or min((r["from"] for r in ok), default=None),
        "overall": summarize_samples([s for r in ok for s in pick(r)], fee_pct),
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
