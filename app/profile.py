"""My profile: the symbols I follow, my position in each and what I expect from it.

Stored as JSON on disk (data/profile.json, or $PROFILE_PATH) and compared live against the chart outlook.
"""
from __future__ import annotations

import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from . import backtest
from .screener import _scan_one
from .sizing import position_risk, size_position

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "profile.json"
_lock = threading.Lock()


class Entry(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    position: Literal["long", "short", "none"] = "none"  # none = watching only
    qty: Optional[float] = Field(None, ge=0)
    avgCost: Optional[float] = Field(None, gt=0)
    expectation: Literal["bullish", "bearish", "neutral"] = "neutral"
    target: Optional[float] = Field(None, gt=0)
    stop: Optional[float] = Field(None, gt=0)
    horizon: str = Field("", max_length=40)
    note: str = Field("", max_length=500)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class Settings(BaseModel):
    """My money and my rules for sizing positions."""
    capital: Optional[float] = Field(None, gt=0)
    currency: str = Field("EGP", min_length=1, max_length=8)
    riskPct: float = Field(1.0, gt=0, le=10)  # max loss per trade, % of capital
    maxPositionPct: float = Field(25.0, gt=0, le=100)  # max size of one stock, % of capital

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class Profile(BaseModel):
    name: str = Field("", max_length=60)
    settings: Settings = Settings()
    entries: list[Entry] = []


def _path() -> Path:
    return Path(os.environ.get("PROFILE_PATH") or DEFAULT_PATH)


def load() -> Profile:
    p = _path()
    if not p.exists():
        return Profile()
    return Profile.model_validate_json(p.read_text(encoding="utf-8"))


def save(profile: Profile) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(profile.model_dump(), indent=2), encoding="utf-8")
    tmp.replace(p)


def set_name(name: str) -> Profile:
    with _lock:
        prof = load()
        prof.name = name.strip()
        save(prof)
    return prof


def set_settings(settings: Settings) -> Profile:
    with _lock:
        prof = load()
        prof.settings = settings
        save(prof)
    return prof


def sizing_for(settings: Settings, currency: str | None, entry: float, stop: float | None) -> dict:
    """How much to buy at `entry` with `stop`, under my rules; explains when it can't be sized."""
    if not settings.capital:
        return {"ok": False, "configured": False, "reason": "Set your capital in My profile to see position sizes."}
    if not currency:
        return {"ok": False, "configured": True, "reason": "An index can't be bought directly (use a fund that tracks it)."}
    if currency.upper() != settings.currency:
        return {"ok": False, "configured": True, "reason": f"Your capital is in {settings.currency}; this is priced in {currency}."}
    if stop is None:
        return {"ok": False, "configured": True, "reason": "Needs a stop-loss price to size the position."}
    return {"configured": True, "currency": settings.currency} | size_position(
        settings.capital, settings.riskPct, settings.maxPositionPct, entry, stop)


def upsert(entry: Entry) -> Profile:
    with _lock:
        prof = load()
        prof.entries = [e for e in prof.entries if e.symbol != entry.symbol] + [entry]
        save(prof)
    return prof


def remove(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    with _lock:
        prof = load()
        kept = [e for e in prof.entries if e.symbol != symbol]
        if len(kept) == len(prof.entries):
            return False
        prof.entries = kept
        save(prof)
    return True


def _app_view(score: float) -> str:
    """Same thresholds as the outlook's bias label."""
    return "bullish" if score >= 0.4 else "bearish" if score <= -0.4 else "neutral"


def _pct(a: float, b: float) -> float:
    return round((a - b) / b * 100, 2)


def compare(entry: Entry, live: dict, track: dict | None = None, settings: Settings | None = None) -> dict:
    """Merge my entry with the live scan row: P/L, distance to my levels, and whether the chart agrees."""
    out = entry.model_dump()
    if "error" in live:
        return out | {"error": live["error"]}
    last = live["last"]
    sign = {"long": 1, "short": -1}.get(entry.position, 0)
    view = _app_view(live["score"])

    status = "open"
    # Which way my target/stop point: a held position's stop protects the position; when only
    # watching, they follow my expectation.
    up = entry.position == "long" or (entry.position == "none" and entry.expectation != "bearish")
    # A passed stop outranks a reached target: it's the one that needs action.
    if entry.stop is not None and (last <= entry.stop if up else last >= entry.stop):
        status = "stop hit"
    elif entry.target is not None and (last >= entry.target if up else last <= entry.target):
        status = "target hit"

    if view == entry.expectation:
        agreement = "agrees"
    elif "neutral" in (view, entry.expectation):
        agreement = "mixed"
    else:
        agreement = "disagrees"

    res = out | {
        "resolved": live["symbol"],
        "name": live["name"],
        "currency": live["currency"],
        "last": last,
        "changePct": live["changePct"],
        "plPct": _pct(last, entry.avgCost) * sign if sign and entry.avgCost else None,
        "plValue": round((last - entry.avgCost) * entry.qty * sign, 2) if sign and entry.avgCost and entry.qty else None,
        "toTargetPct": _pct(entry.target, last) if entry.target else None,
        "toStopPct": _pct(entry.stop, last) if entry.stop else None,
        "status": status,
        "appView": view,
        "appBias": live["bias"],
        "appScore": live["score"],
        "appTarget": live["upTarget"] if up else live["downTarget"],
        "appAction": live["action"],
        "appSignal": live["signal"],
        "agreement": agreement,
        "track": _track_line(track, live["bias"]),
        **_money(entry, live, settings or Settings()),
    }
    return res | {"action": action_for(entry, res, settings or Settings())}


def _money(entry: Entry, live: dict, settings: Settings) -> dict:
    """Sizing for a new/added buy, and what an open position means for the account."""
    last = live["last"]
    mine = entry.stop is not None and entry.stop < last
    stop = entry.stop if mine else live.get("chartStop")
    out = {"sizing": None, "exposure": None}
    if entry.position != "short":
        out["sizing"] = sizing_for(settings, live["currency"], last, stop) | {"stopSource": "yours" if mine else "chart"}
    same_money = settings.capital and live["currency"] and live["currency"].upper() == settings.currency
    if entry.position != "none" and entry.qty and same_money:
        out["exposure"] = position_risk(settings.capital, entry.position, entry.qty, last, entry.stop)
    return out


def _track_line(track: dict | None, bias: str) -> dict | None:
    """How the chart's current bias has played out on this symbol before."""
    if not track:
        return None
    b = next((b for b in track["buckets"] if b["bias"] == bias), None)
    if not b or not b["n"]:
        return None
    return {k: b[k] for k in ("bias", "n", "asExpected", "avgRet", "excess", "small")} | {"baseline": track["baseline"]}


GROUP = {"buy": "Buy", "sell": "Sell", "hold": "Hold", "caution": "Hold", "wait": "Wait"}


def _act(action: str, tone: str, reason: str, **extra) -> dict:
    """`action` is the specific tag (e.g. "Cut loss"); `group` is the plain Buy / Hold / Sell / Wait."""
    return {"action": action, "tone": tone, "group": GROUP[tone], "reason": reason} | extra


NEAR_STOP_PCT = 3.0


def _room_to_add(entry: Entry, last: float, settings: Settings) -> int:
    """Extra shares my rules still allow on top of what I hold (0 if none or unknown)."""
    if not (settings.capital and entry.qty and entry.stop and entry.stop < last):
        return 0
    z = size_position(settings.capital, settings.riskPct, settings.maxPositionPct, last, entry.stop)
    return max(0, z["shares"] - int(entry.qty)) if z["ok"] else 0


def action_for(entry: Entry, e: dict, settings: Settings) -> dict:
    """Buy / Hold / Sell from my own plan (view, stop, target) and my money rules.

    The chart's bias is deliberately not an input: the track record shows it has no reliable edge.
    """
    last, stop, target = e["last"], entry.stop, entry.target
    f = lambda x: f"{x:,.2f}"

    if entry.position == "short":
        if e["status"] == "stop hit":
            return _act("Cover (stop)", "sell", f"The price rose past your stop ({f(stop)}). Your plan says buy back and close the short.")
        if e["status"] == "target hit":
            return _act("Take profit", "sell", f"The price reached your target ({f(target)}). Buy back and take the profit.")
        return _act("Hold", "hold", "Your short is between its stop and target.")

    if entry.position == "long":
        if e["status"] == "stop hit":
            if entry.avgCost and last < entry.avgCost:
                return _act("Cut loss", "sell", f"The price broke your stop ({f(stop)}). Your plan says exit now, to keep a small loss from growing.")
            return _act("Exit (stop)", "sell", f"The price fell back through your stop ({f(stop)}). Exit and keep the gain that's left.")
        if e["status"] == "target hit":
            return _act("Take profit", "sell", f"The price reached your target ({f(target)}). Take the profit, or move your stop up and hold for more.")
        if entry.expectation == "bearish":
            return _act("Sell", "sell", "You expect it to fall but still hold it. Sell, or update your view if it has changed.")
        x = e.get("exposure")
        if x and entry.qty and settings.capital:
            cap = math.floor(settings.capital * settings.maxPositionPct / 100 / last)
            by_risk = math.floor(settings.capital * settings.riskPct / 100 / (last - stop)) if stop and stop < last else None
            allowed = min(cap, by_risk) if by_risk is not None else cap
            too_big = x["weightPct"] > settings.maxPositionPct
            too_risky = x["riskPct"] is not None and x["riskPct"] > settings.riskPct * 1.5
            if (too_big or too_risky) and allowed < entry.qty:
                why = (f"it is {x['weightPct']:g}% of your capital (limit {settings.maxPositionPct:g}%)" if too_big
                       else f"hitting your stop would cost {x['riskPct']:g}% of your capital (rule {settings.riskPct:g}%)")
                return _act("Trim", "sell", f"Too much in one stock: {why}. Sell {entry.qty - allowed:,.0f} to get back within your rules.",
                            shares=int(entry.qty - allowed))
        if stop is None:
            z = e.get("sizing") or {}
            hint = f" The chart suggests about {f(z['stop'])}." if z.get("stop") else ""
            return _act("Set stop", "caution", f"Nothing says sell, but you have no stop-loss. Set one now.{hint}")
        if e.get("toStopPct") is not None and abs(e["toStopPct"]) <= NEAR_STOP_PCT:
            return _act("Watch closely", "caution", f"Only {abs(e['toStopPct']):.1f}% above your stop ({f(stop)}). Be ready to exit if it closes below.")
        extra = _room_to_add(entry, last, settings)
        if entry.expectation == "bullish" and target and extra and (target - last) >= (last - stop):
            return _act("Add more", "buy", f"Your view is still bullish, your target ({f(target)}) offers more than your stop risks, "
                                           f"and your rules allow up to {extra:,} more shares.", shares=extra)
        between = f"between your stop ({f(stop)})" + (f" and target ({f(target)})" if target else "")
        return _act("Hold", "hold", f"The price is {between}. Your plan says stay in.")

    # watching
    if entry.expectation != "bullish":
        return _act("Watch", "wait", "Your view isn't bullish, so there's no reason to buy yet.")
    if stop is not None and last <= stop:
        return _act("Wait", "wait", f"The price is already below your stop level ({f(stop)}); your idea isn't working yet.")
    if target is not None and last >= target:
        return _act("Wait", "wait", f"The price is already at your target ({f(target)}), so your plan has no upside left.")
    z = e.get("sizing") or {}
    if target is not None and z.get("stop") and last > z["stop"]:
        rr = (target - last) / (last - z["stop"])
        if rr < 1:
            return _act("Wait for dip", "wait", f"Your target gains less ({f(target - last)}/share) than the stop risks ({f(last - z['stop'])}/share). Wait for a better price.")
    if z.get("ok"):
        whose = "your stop" if z.get("stopSource") == "yours" else "a stop"
        return _act("Buy", "buy", f"Your view is bullish. Buy up to {z['shares']:,} shares with {whose} at {f(z['stop'])}; "
                                  f"that risks {z['riskPct']:g}% of your capital.", shares=z["shares"])
    if z.get("configured") is False:
        return _act("Buy", "buy", "Your view is bullish. Set your capital in My money & rules to see how many shares.")
    if z.get("reason", "").startswith("An index"):
        return _act("Buy via fund", "buy", "Your view is bullish. An index is bought through a fund that tracks it.")
    if "Even one share" in z.get("reason", "") or "One share costs" in z.get("reason", ""):
        return _act("Wait", "wait", z["reason"])
    return _act("Buy", "buy", f"Your view is bullish. {z.get('reason', '')}".strip())




def summarize(entries: list[dict], settings: Settings | None = None) -> dict:
    """Totals per currency, how often the chart agrees with me, and what needs a look."""
    held = [e for e in entries if e["position"] != "none"]
    books: dict[str, dict] = {}
    for e in held:
        if e.get("plValue") is None:
            continue
        cost = e["avgCost"] * e["qty"]
        b = books.setdefault(e.get("currency") or "", {"currency": e.get("currency") or "", "positions": 0, "cost": 0.0, "pl": 0.0})
        b["positions"] += 1
        b["cost"] += cost
        b["pl"] += e["plValue"]
    by_currency = [
        b | {"cost": round(b["cost"], 2), "pl": round(b["pl"], 2), "value": round(b["cost"] + b["pl"], 2),
             "plPct": round(b["pl"] / b["cost"] * 100, 2) if b["cost"] else None}
        for b in sorted(books.values(), key=lambda b: -b["cost"])
    ]

    ranked = sorted((e for e in held if e.get("plPct") is not None), key=lambda e: e["plPct"])
    pick = lambda e: {"symbol": e["symbol"], "plPct": e["plPct"]}

    attention = []
    for e in entries:
        s = e["symbol"]
        if "error" in e:
            attention.append({"symbol": s, "kind": "error", "text": "No market data for this symbol."})
        elif e["status"] == "stop hit":
            attention.append({"symbol": s, "kind": "stop", "text": f"Price is past your stop ({e['stop']:g})."})
        elif e["status"] == "target hit":
            attention.append({"symbol": s, "kind": "target", "text": f"Reached your target ({e['target']:g})."})
        elif e["position"] != "none" and e.get("toStopPct") is not None and abs(e["toStopPct"]) <= NEAR_STOP_PCT:
            attention.append({"symbol": s, "kind": "near-stop", "text": f"Within {abs(e['toStopPct']):.1f}% of your stop ({e['stop']:g})."})
        if e["position"] != "none" and e.get("agreement") == "disagrees":
            attention.append({"symbol": s, "kind": "disagree",
                              "text": f"You hold it expecting {e['expectation']}, but the chart reads {e['appBias'].lower()}."})

    settings = settings or Settings()
    risk = None
    if settings.capital:
        exp = [(e, e["exposure"]) for e in entries if e.get("exposure")]
        invested = sum(x["value"] for _, x in exp)
        risk = {
            "capital": settings.capital,
            "currency": settings.currency,
            "riskPct": settings.riskPct,
            "maxPositionPct": settings.maxPositionPct,
            "invested": round(invested, 2),
            "investedPct": round(invested / settings.capital * 100, 1),
            "cash": round(settings.capital - invested, 2),
            "atRisk": round(sum(x["risk"] or 0 for _, x in exp), 2),
            "atRiskPct": round(sum(x["risk"] or 0 for _, x in exp) / settings.capital * 100, 2),
            "noStop": [e["symbol"] for e, x in exp if x["risk"] is None],
        }
        for e, x in exp:
            s = e["symbol"]
            if x["weightPct"] > settings.maxPositionPct:
                attention.append({"symbol": s, "kind": "big",
                                  "text": f"Is {x['weightPct']:g}% of your capital, above your {settings.maxPositionPct:g}% limit per stock."})
            if x["riskPct"] is None:
                attention.append({"symbol": s, "kind": "no-stop", "text": "Has no stop-loss, so there's no limit on what it can cost you."})
            elif x["riskPct"] > settings.riskPct * 1.5:
                attention.append({"symbol": s, "kind": "risk",
                                  "text": f"Hitting your stop would cost {x['riskPct']:g}% of your capital; your rule is {settings.riskPct:g}%."})

    count = lambda k, v: sum(1 for e in entries if e.get(k) == v)
    todo: dict[str, list[str]] = {"buy": [], "hold": [], "sell": [], "wait": []}
    for e in entries:
        a = e.get("action")
        if a:
            todo[a["group"].lower()].append(e["symbol"])
    return {
        "todo": todo,
        "risk": risk,
        "count": len(entries),
        "holding": len(held),
        "watching": len(entries) - len(held),
        "byCurrency": by_currency,
        "best": pick(ranked[-1]) if ranked else None,
        "worst": pick(ranked[0]) if len(ranked) > 1 else None,
        "expectation": {k: count("expectation", k) for k in ("bullish", "neutral", "bearish")},
        "agreement": {k: count("agreement", k) for k in ("agrees", "mixed", "disagrees")},
        "attention": attention,
    }


def live_profile(interval: str, period: str) -> dict:
    prof = load()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda e: _scan_one(e.symbol, interval, period), prof.entries))
    entries = [compare(e, r, backtest.track_for(r["symbol"], interval) if "error" not in r else None, prof.settings)
               for e, r in zip(prof.entries, rows)]
    return {"name": prof.name, "settings": prof.settings.model_dump(), "entries": entries,
            "summary": summarize(entries, prof.settings)}
