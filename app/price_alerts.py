"""Price alerts set on the chart: a Telegram message when price reaches a level.

Kept in data/price_alerts.json. Checked every minute while the app runs, at any hour (gold and FX trade
when EGX is closed); prices are cached for 5 minutes, so a hit is noticed within about that. A level
counts as reached when the latest price is at or past it, or when any bar that started after the alert
was set touched it, so a quick spike between checks isn't missed.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
from pydantic import BaseModel, Field

from . import alerts, profile
from .data import fetch_ohlcv

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "price_alerts.json"
CHECK_EVERY = 60  # seconds between checks
_lock = threading.Lock()

Fetch = Callable[[str], pd.DataFrame]


class NewAlert(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=40)
    price: float = Field(..., gt=0)
    note: str = Field("", max_length=120)
    name: str = Field("", max_length=120)


def _path() -> Path:
    return Path(os.environ.get("PRICE_ALERTS") or DEFAULT_PATH)


def load() -> list[dict]:
    p = _path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(items: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:,.5f}"


def _bars(symbol: str) -> pd.DataFrame:
    """Hourly bars when the symbol has them (finer for spotting a touch), else daily."""
    try:
        return fetch_ohlcv(symbol, "1h", "3mo")[1]
    except Exception:
        return fetch_ohlcv(symbol, "1d", "3mo")[1]


def add(new: NewAlert, last: float | None = None) -> dict:
    """Store an alert. Whether it waits for a rise or a fall depends on which side of the price it's set."""
    if last is None:
        last = float(fetch_ohlcv(new.symbol, "1d", "3mo")[1]["Close"].iloc[-1])
    item = {"id": uuid.uuid4().hex[:10], "symbol": new.symbol, "name": new.name or new.symbol, "price": new.price,
            "direction": "above" if new.price > last else "below", "note": new.note.strip(),
            "createdAt": _now(), "createdPrice": round(last, 6), "triggeredAt": None, "triggeredPrice": None,
            "sent": False, "error": None}
    with _lock:
        items = load()
        items.append(item)
        _save(items)
    return item


def remove(alert_id: str) -> bool:
    with _lock:
        items = load()
        kept = [a for a in items if a["id"] != alert_id]
        _save(kept)
    return len(kept) < len(items)


def for_symbol(symbol: str) -> list[dict]:
    return [a for a in load() if a["symbol"] == symbol]


def hit_price(a: dict, df: pd.DataFrame) -> float | None:
    """The price that reached the alert's level, or None if it hasn't."""
    up = a["direction"] == "above"
    reached = (lambda x: x >= a["price"]) if up else (lambda x: x <= a["price"])
    last = float(df["Close"].iloc[-1])
    if reached(last):
        return last
    created = pd.Timestamp(a["createdAt"])
    idx = df.index
    after = df[idx > (created.tz_convert(idx.tz) if idx.tz is not None else created.tz_convert(None))]
    if len(after):
        extreme = float(after["High"].max()) if up else float(after["Low"].min())
        if reached(extreme):
            return extreme
    return None


def message(a: dict, hit: float) -> str:
    moved = "rose to" if a["direction"] == "above" else "fell to"
    text = f"🔔 {a['name']}: {moved} {_fmt(hit)}, reaching your alert at {_fmt(a['price'])}"
    return text + (f"\n📝 {a['note']}" if a["note"] else "")


def check_once(send: alerts.Sender | None = None, fetch: Fetch | None = None) -> dict:
    """Check every waiting alert once and message the ones that were hit. Returns what happened."""
    send = send or alerts.send_telegram  # looked up at call time so tests can stub it
    fetch = fetch or _bars
    waiting = [a for a in load() if not a["triggeredAt"]]
    hits: dict[str, float] = {}
    errors: dict[str, str] = {}
    for sym in {a["symbol"] for a in waiting}:  # prices are fetched outside the lock: adding an alert mustn't wait on it
        try:
            df = fetch(sym)
        except Exception as e:
            errors.update({a["id"]: f"No price: {str(e)[:80]}" for a in waiting if a["symbol"] == sym})
            continue
        for a in waiting:
            if a["symbol"] == sym and (h := hit_price(a, df)) is not None:
                hits[a["id"]] = h
    s = profile.load().settings
    sent = 0
    with _lock:
        items = load()  # again: alerts may have been added or removed meanwhile
        for a in items:
            if a["triggeredAt"]:
                continue
            if a["id"] in errors:
                a["error"] = errors[a["id"]]
            if a["id"] not in hits:
                continue
            a.update(triggeredAt=_now(), triggeredPrice=round(hits[a["id"]], 6), error=None)
            if not (s.telegramToken and s.telegramChatId):
                a["error"] = "Hit, but Telegram isn't set up (My profile → Alerts)."
                continue
            try:
                send(s.telegramToken, s.telegramChatId, message(a, hits[a["id"]]))
                a["sent"] = True
                sent += 1
            except Exception as e:
                a["error"] = f"Hit, but the message failed: {str(e)[:120]}"
        _save(items)
    return {"checked": len(waiting), "hit": len(hits), "sent": sent}


def _loop() -> None:
    while True:
        try:
            if any(not a["triggeredAt"] for a in load()):
                check_once()
        except Exception:
            pass  # never let the background thread die
        time.sleep(CHECK_EVERY)


def start_background() -> None:
    if os.environ.get("PATTERN_NO_ALERTS"):
        return
    threading.Thread(target=_loop, name="price-alerts", daemon=True).start()
