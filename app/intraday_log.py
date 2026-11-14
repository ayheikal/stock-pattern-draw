"""Live record of the intraday calls: every 5/5 setup the app shows is written down when it appears, then scored.

A backtest can be fooled by hindsight; this can't. A signal is logged from a scan (yours, or the automatic one
during EGX hours when switched on) while its bar is still fresh, and scored like the backtest scores it: in at the
next bar's open, ATR stop and target, out by the session's close (intraday.trade). Kept in data/intraday_log.json.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pandas as pd

from . import alerts, intraday, profile
from .data import fetch_ohlcv
from .intraday_backtest import SLIPPAGE_PCT, summarize
from .screener import get_universe

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
FRESH_MIN = 30        # a signal is logged only if its bar closed within this many minutes (the feed can run ~15 late)
SCORE_EVERY_MIN = 5   # how often open signals are checked against new bars
DONE = ("target", "stop", "time")
_lock = threading.RLock()


def _file() -> Path:
    return Path(os.environ.get("INTRADAY_DIR") or DEFAULT_DIR) / "intraday_log.json"


def load() -> dict:
    p = _file()
    st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {"auto": {"on": False, "universe": "egx", "interval": "15m"}, "signals": [], "lastAuto": None, "lastScore": None,
            "lastError": None} | st


def _save(st: dict) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(p)


def set_auto(on: bool, universe: str, interval: str) -> dict:
    with _lock:
        st = load()
        st["auto"] = {"on": on, "universe": universe, "interval": interval}
        _save(st)
        return st["auto"]


def record(rows: list[dict], interval: str, source: str, now: pd.Timestamp | None = None) -> int:
    """Log each row passing all five rules whose bar is fresh; one open signal per stock and bar size. Returns how many were added."""
    now = now or pd.Timestamp.now(tz="UTC")
    added = 0
    with _lock:
        st = load()
        ids = {s["id"] for s in st["signals"]}
        busy = {(s["symbol"], s["interval"]) for s in st["signals"] if s["status"] == "open"}
        for r in rows:
            if r.get("passed") != 5 or not r.get("barTime") or (r["symbol"], interval) in busy:
                continue
            closed = pd.Timestamp(r["barTime"]) + pd.Timedelta(minutes=intraday.BAR_MINUTES[interval])
            sid = f"{r['symbol']}|{interval}|{r['barTime']}"
            if sid in ids or now - closed > pd.Timedelta(minutes=FRESH_MIN):
                continue
            st["signals"].append({"id": sid, "symbol": r["symbol"], "name": r.get("name") or r["symbol"], "interval": interval,
                                  "barTime": r["barTime"], "lastBar": r["lastBar"], "close": r["last"], "entryType": r.get("entry"),
                                  "source": source, "loggedAt": now.isoformat(timespec="seconds"), "status": "open"})
            busy.add((r["symbol"], interval))
            added += 1
        if added:
            _save(st)
    return added


def _score_one(sig: dict, now: pd.Timestamp, fetch) -> dict:
    """The signal with its outcome filled in when it's known."""
    _, df, _ = fetch(sig["symbol"], sig["interval"], intraday.INTERVALS[sig["interval"]])
    df = intraday._completed(df, sig["interval"], now)
    bar = pd.Timestamp(sig["barTime"])
    pos = df.index.get_indexer([bar])[0]
    if pos < 0:  # the bar has dropped out of the history the source still gives
        return sig | {"status": "expired"} if len(df) and df.index[0] > bar else sig
    day_over = now.tz_convert(df.index.tz).normalize() > bar.tz_convert(df.index.tz).normalize()
    t = intraday.trade(intraday.signals(df), int(pos), day_over=day_over)
    if t["outcome"] == "pending":
        return sig | {"status": "skipped"} if day_over else sig
    if t["outcome"] == "skipped":
        return sig | {"status": "skipped"}
    out = sig | {k: round(t[k], 4) for k in ("entry", "stop", "target")}
    if t["outcome"] == "open":
        return out
    return out | {"status": t["outcome"], "exit": round(t["exit"], 4), "R": round(t["R"], 3), "retPct": round(t["retPct"], 4),
                  "riskPct": round(t["riskPct"], 4), "exitTime": df.index[t["exitIdx"]].strftime("%Y-%m-%d %H:%M")}


def score(now: pd.Timestamp | None = None, fetch=None) -> int:
    """Check every open signal against the bars since; returns how many were settled."""
    now = now or pd.Timestamp.now(tz="UTC")
    fetch = fetch or fetch_ohlcv
    settled = 0
    with _lock:
        st = load()
        for i, sig in enumerate(st["signals"]):
            if sig["status"] != "open":
                continue
            try:
                new = _score_one(sig, now, fetch)
            except Exception as e:  # no data right now: try again next time
                st["lastError"] = f"{sig['symbol']}: {str(e)[:80]}"
                continue
            settled += new["status"] != "open"
            st["signals"][i] = new
        st["lastScore"] = now.isoformat(timespec="seconds")
        _save(st)
    return settled


def report(fee_pct: float, limit: int = 100) -> dict:
    """The record so far: results per bar size (same figures as the backtest) and the newest signals."""
    st = load()
    cost = 2 * (fee_pct + SLIPPAGE_PCT)
    as_trade = lambda s: {"at": s["lastBar"], "outcome": s["status"], "R": s["R"], "retPct": s["retPct"], "riskPct": s["riskPct"]}
    done = [s for s in st["signals"] if s["status"] in DONE]
    groups = [("all", "All bar sizes", done)] + [(k, k, [s for s in done if s["interval"] == k]) for k in intraday.INTERVALS]
    return {
        "auto": st["auto"],
        "lastAuto": st["lastAuto"],
        "lastScore": st["lastScore"],
        "costPct": round(cost, 3),
        "open": sum(1 for s in st["signals"] if s["status"] == "open"),
        "total": len(st["signals"]),
        "summary": [{"key": k, "label": label, **summarize([as_trade(s) for s in ss], cost)} for k, label, ss in groups if ss or k == "all"],
        "signals": sorted(st["signals"], key=lambda s: s["barTime"], reverse=True)[:limit],
    }


def auto_scan(now: pd.Timestamp | None = None) -> int:
    """One automatic scan of the chosen watchlist; logs what passes all five."""
    st = load()
    a = st["auto"]
    uni = get_universe(a["universe"])
    if not uni:
        return 0
    out = intraday.scan(uni["symbols"], a["interval"])
    return record(out["results"], a["interval"], "auto", now)


def _due(last: str | None, minutes: float, now: pd.Timestamp) -> bool:
    return last is None or (now - pd.Timestamp(last)).total_seconds() >= minutes * 60


def tick() -> None:
    """One pass of the background loop: scan when it's switched on and the EGX is open, and settle open signals."""
    now = pd.Timestamp.now(tz="UTC")
    st = load()
    a = st["auto"]
    if a["on"] and alerts.in_session(alerts.cairo_now(), profile.load().settings.holidays) \
            and _due(st["lastAuto"], intraday.BAR_MINUTES[a["interval"]], now):
        with _lock:
            st = load()
            st["lastAuto"] = now.isoformat(timespec="seconds")
            _save(st)
        auto_scan(now)
    st = load()
    if any(s["status"] == "open" for s in st["signals"]) and _due(st["lastScore"], SCORE_EVERY_MIN, now):
        score(now)


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # never let the background thread die
            try:
                with _lock:
                    st = load()
                    st["lastError"] = str(e)[:120]
                    _save(st)
            except Exception:
                pass
        time.sleep(60)


def start_background() -> None:
    if os.environ.get("PATTERN_NO_ALERTS"):
        return
    threading.Thread(target=_loop, name="intraday-log", daemon=True).start()
