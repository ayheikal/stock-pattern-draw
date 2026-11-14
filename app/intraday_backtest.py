"""Do the intraday rules make money? Replay them over the intraday history there is, and count what each trade did.

At every bar the rules see that bar and earlier ones only (intraday.signals). A signal buys the next bar's open,
with the ATR stop and target, and is out by the session's close (intraday.trade). One trade at a time per stock.
Results are per rule set: all five rules, four or more, all five but one (does that rule add anything?), and
every bar with no rules at all, which is what the rules have to beat. Costs: the fee from My profile plus
SLIPPAGE_PCT, on the buy and on the sell.

CLI:  python -m app.intraday_backtest egx 15m
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import intraday
from .data import InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .screener import get_universe

# The most history the sources give: Yahoo keeps 60 days of 5m / 15m bars, TradingView about 5,000 bars.
HISTORY = {"5m": "3mo", "15m": "6mo", "1h": "2y"}
SLIPPAGE_PCT = 0.1   # assumed gap between the price you see and the price you get, per side
MIN_RELIABLE = 30    # fewer trades than this prove nothing either way

STRATEGIES = ([("all5", "All 5 rules"), ("4plus", "4 or more rules")]
              + [(f"no_{k}", f"All but {label}") for k, label in intraday.RULES.items()]
              + [("any", "Every bar (no rules)")])

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
_running: set[tuple[str, str]] = set()
_errors: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


def masks(s: dict) -> dict:
    """Bars where each rule set says buy."""
    ok = s["ok"]
    out = {"all5": s["passed"] == 5, "4plus": s["passed"] >= 4, "any": s["passed"] >= 0}
    for k in intraday.RULES:
        out[f"no_{k}"] = (s["passed"] - ok[k].astype(int)) == 4
    return out


def replay(df: pd.DataFrame) -> dict[str, list[dict]]:
    """Each rule set's trades on one stock's bars, never two at once."""
    s = intraday.signals(df)
    times = df.index.strftime("%Y-%m-%d %H:%M")
    out: dict[str, list[dict]] = {}
    for key, mask in masks(s).items():
        trades, free_from = [], intraday.MIN_BARS
        for i in mask.nonzero()[0]:
            if i < free_from:
                continue
            t = intraday.trade(s, int(i), day_over=False)
            if t["outcome"] in ("skipped", "pending", "open"):
                continue
            free_from = t["exitIdx"] + 1
            trades.append({"at": times[i], "outcome": t["outcome"], "R": round(t["R"], 3), "retPct": round(t["retPct"], 4),
                           "riskPct": round(t["riskPct"], 4)})
        out[key] = trades
    return out


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _share(xs: list, pred) -> float | None:
    return round(sum(1 for x in xs if pred(x)) / len(xs) * 100, 1) if xs else None


def verdict(n: int, avg: float | None, sd: float | None) -> str:
    """profit / loss when the average result after costs is twice its standard error away from zero,
    unclear when it isn't, few under MIN_RELIABLE trades."""
    if n < MIN_RELIABLE or avg is None or not sd:
        return "few"
    t = avg / (sd / math.sqrt(n))
    return "profit" if t >= 2 else "loss" if t <= -2 else "unclear"


def summarize(trades: list[dict], cost_pct: float) -> dict:
    """`cost_pct` is the whole round trip (buy + sell), in % of the trade."""
    net = [t["retPct"] - cost_pct for t in trades]
    n = len(net)
    avg = sum(net) / n if n else None
    sd = math.sqrt(sum((x - avg) ** 2 for x in net) / (n - 1)) if n > 1 else None
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    order = sorted(range(n), key=lambda i: trades[i]["at"])
    half = [net[i] for i in order[: n // 2]], [net[i] for i in order[n // 2:]]
    return {
        "n": n,
        "win": _share(net, lambda x: x > 0),
        "target": _share(trades, lambda t: t["outcome"] == "target"),
        "stop": _share(trades, lambda t: t["outcome"] == "stop"),
        "time": _share(trades, lambda t: t["outcome"] == "time"),
        "avgR": _mean([t["R"] for t in trades]),
        "avgRet": _mean([t["retPct"] for t in trades]),
        "avgNet": _mean(net),
        "totalNet": round(sum(net), 2) if n else None,
        "profitFactor": round(gains / losses, 2) if losses > 0 else None,
        "avgRisk": _mean([t["riskPct"] for t in trades]),
        "firstHalf": _mean(half[0]),
        "secondHalf": _mean(half[1]),
        "verdict": verdict(n, avg, sd),
    }


def _replay_symbol(symbol: str, interval: str) -> dict:
    try:
        resolved, df, meta = fetch_ohlcv(symbol, interval, HISTORY[interval])
    except (SymbolNotFound, InsufficientHistory):
        return {"symbol": symbol, "error": "no data"}
    except Exception as e:  # one bad symbol shouldn't sink the run
        return {"symbol": symbol, "error": str(e)[:80]}
    if not meta.get("currency") or df["Volume"].sum() <= 0:
        return {"symbol": symbol, "error": "an index: no volume"}
    df = intraday._completed(df, interval, None)
    if len(df) < 5 * intraday.MIN_BARS:
        return {"symbol": symbol, "error": f"only {len(df)} bars"}
    return {"symbol": symbol, "resolved": resolved, "name": meta.get("longName") or meta.get("shortName") or resolved,
            "from": df.index[0].strftime("%Y-%m-%d"), "to": df.index[-1].strftime("%Y-%m-%d"), "bars": len(df),
            "trades": replay(df)}


def run(universe: str, interval: str, symbols: list[str] | None = None, fee_pct: float = 0.0) -> dict:
    symbols = symbols or get_universe(universe)["symbols"]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda sym: _replay_symbol(sym, interval), symbols))
    ok = [r for r in rows if "error" not in r]
    cost = 2 * (fee_pct + SLIPPAGE_PCT)
    every = lambda key: [t for r in ok for t in r["trades"][key]]
    return {
        "universe": universe,
        "interval": interval,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - t0, 1),
        "symbols": len(ok),
        "skipped": [f"{r['symbol']} ({r['error']})" for r in rows if "error" in r],
        "from": min((r["from"] for r in ok), default=None),
        "to": max((r["to"] for r in ok), default=None),
        "bars": sum(r["bars"] for r in ok),
        "feePct": fee_pct,
        "slippagePct": SLIPPAGE_PCT,
        "costPct": round(cost, 3),
        "stopAtr": intraday.STOP_ATR,
        "targetAtr": intraday.TARGET_ATR,
        "strategies": [{"key": k, "label": label, **summarize(every(k), cost)} for k, label in STRATEGIES],
        "perSymbol": sorted(({"symbol": r["resolved"], "name": r["name"], **summarize(r["trades"]["all5"], cost)} for r in ok),
                            key=lambda x: -x["n"]),
    }


# --- cache on disk + background runs (as backtest.py does) ---

def _dir() -> Path:
    return Path(os.environ.get("INTRADAY_DIR") or DEFAULT_DIR)


def _file(universe: str, interval: str) -> Path:
    return _dir() / f"intraday_backtest_{universe}_{interval}.json"


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


def start(universe: str, interval: str, fee_pct: float = 0.0) -> bool:
    """Run in a background thread; False if this universe/interval is already running."""
    key = (universe, interval)
    with _lock:
        if key in _running:
            return False
        _running.add(key)
        _errors.pop(key, None)

    def work():
        try:
            save(run(universe, interval, fee_pct=fee_pct))
        except Exception as e:
            with _lock:
                _errors[key] = str(e)[:200]
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=work, daemon=True).start()
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    uni = sys.argv[1] if len(sys.argv) > 1 else "egx"
    iv = sys.argv[2] if len(sys.argv) > 2 else "15m"
    from .profile import load as load_profile  # the CLI uses the fee saved in My profile
    res = run(uni, iv, fee_pct=load_profile().settings.feePct)
    save(res)
    print(f"{res['symbols']} stocks, {res['bars']} bars, {res['from']} to {res['to']}, in {res['seconds']}s; "
          f"round-trip cost {res['costPct']}%; skipped: {', '.join(res['skipped']) or 'none'}")
    for s in res["strategies"]:
        print(f"  {s['label']:<22} n={s['n']:<6} win={s['win']}%  target={s['target']}%  stop={s['stop']}%  "
              f"avgR={s['avgR']}  gross={s['avgRet']}%  net={s['avgNet']}%  {s['verdict']}")
