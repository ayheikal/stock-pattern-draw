"""Telegram alerts for my profile during EGX trading hours.

Stops in this app are mental stops (the broker doesn't see them), so being told matters.
Only *new* items are sent: each check is compared with the previous one (data/alerts_state.json).
Alerts run only while the app is running.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Callable

import httpx
import pandas as pd

from . import profile

SESSION_DAYS = {6, 0, 1, 2, 3}  # Sun-Thu (Python: Mon=0 .. Sun=6)
SESSION_OPEN, SESSION_CLOSE = dtime(10, 0), dtime(14, 45)  # EGX ~10:00-14:30, plus a little slack for late prints
# Actions worth a message when they appear; plain Hold / Wait / Watch are the quiet default.
QUIET_ACTIONS = {"Hold", "Wait", "Watch"}
ICON = {"stop": "⛔", "target": "🎯", "near-stop": "⚠️", "big": "⚖️", "risk": "💸", "no-stop": "🛑", "thin": "💧",
        "earnings": "🗓️", "stale": "⏸️", "total-risk": "💸", "sector": "🧺",
        "sell": "🔴", "buy": "🟢", "caution": "🟠"}

DEFAULT_STATE = Path(__file__).resolve().parent.parent / "data" / "alerts_state.json"
_lock = threading.Lock()

Sender = Callable[[str, str, str], None]


def cairo_now() -> datetime:
    return pd.Timestamp.now(tz="Africa/Cairo").to_pydatetime()


def in_session(now: datetime, holidays: list[date] | tuple = ()) -> bool:
    return now.weekday() in SESSION_DAYS and now.date() not in set(holidays) and SESSION_OPEN <= now.time() <= SESSION_CLOSE


def send_telegram(token: str, chat_id: str, text: str) -> None:
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram said {r.status_code}: {r.text[:120]}")


def collect(live: dict) -> dict[str, str]:
    """Everything currently worth telling me, keyed so the same item isn't sent twice."""
    items: dict[str, str] = {}
    for a in live["summary"]["attention"]:
        if a["kind"] in ("disagree", "error", "date"):  # informational; not worth a phone buzz
            continue
        items[f"{a['symbol']}|{a['kind']}"] = f"{ICON.get(a['kind'], '•')} {a['symbol']}: {a['text']}"
    for e in live["entries"]:
        act = e.get("action")
        if act and act["action"] not in QUIET_ACTIONS:
            items[f"{e['symbol']}|action|{act['action']}"] = f"{ICON.get(act['tone'], '•')} {e['symbol']} → {act['action']}: {act['reason']}"
    return items


def _state_path() -> Path:
    return Path(os.environ.get("ALERTS_STATE") or DEFAULT_STATE)


def load_state() -> dict:
    p = _state_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"active": [], "lastCheck": None, "lastSent": None, "lastError": None}


def _save_state(st: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")


def check_once(send: Sender | None = None, live: dict | None = None) -> dict:
    """Re-check the profile and send what's new since the last check. Returns what happened."""
    send = send or send_telegram  # looked up at call time so tests can stub it
    with _lock:
        st = load_state()
        settings = profile.load().settings
        stamp = cairo_now().isoformat(timespec="seconds")
        try:
            live = live or profile.live_profile("1d", "1y")
            items = collect(live)
            new = [text for key, text in items.items() if key not in set(st["active"])]
            if new:
                if not (settings.telegramToken and settings.telegramChatId):
                    raise RuntimeError("Telegram isn't set up: add the bot token and chat id.")
                send(settings.telegramToken, settings.telegramChatId, "📊 Market Lens\n\n" + "\n\n".join(new))
                st["lastSent"] = stamp
            st.update(active=sorted(items), lastCheck=stamp, lastError=None)
            _save_state(st)
            return {"ok": True, "sent": len(new), "active": len(items)}
        except Exception as e:  # keep the loop alive; show the problem on the page
            st.update(lastCheck=stamp, lastError=str(e)[:200])
            _save_state(st)
            return {"ok": False, "error": st["lastError"]}


def send_test(send: Sender | None = None) -> None:
    send = send or send_telegram
    s = profile.load().settings
    if not (s.telegramToken and s.telegramChatId):
        raise RuntimeError("Add the bot token and chat id first.")
    send(s.telegramToken, s.telegramChatId, "📊 Market Lens: test message ✓ Alerts will arrive here.")


def _loop() -> None:
    while True:
        try:
            s = profile.load().settings
            st = load_state()
            now = cairo_now()
            last = datetime.fromisoformat(st["lastCheck"]) if st.get("lastCheck") else None
            due = last is None or (now - last).total_seconds() >= s.alertEveryMin * 60
            if s.alertsOn and in_session(now, s.holidays) and due:
                check_once()
        except Exception:
            pass  # never let the background thread die
        time.sleep(60)


def start_background() -> None:
    if os.environ.get("PATTERN_NO_ALERTS"):
        return
    threading.Thread(target=_loop, name="alerts", daemon=True).start()
