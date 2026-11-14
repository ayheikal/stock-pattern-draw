"""Pick a trade: the app's pages walked in order, ending in a trade plan or a plain "no trade today".

    1 market   is this a day to buy at all? (the market mood the final decision already uses)
    2 sectors  where the strength is
    3 shortlist  every stock in a watchlist whose final call is a buy (final.buy_list)
    4 plan     per stock: shares, entry, stop, target, what speaks for it, what to watch
    5 timing   the intraday rules, only to time a buy already decided
    6 watch    the stocks nearest to a buy, and what each is waiting for

Nothing here judges a stock: it lines up verdicts the other modules reached and says where they are shaky
(a thin reward for the risk, prices from the fallback source, a market with no tailwind).
"""
from __future__ import annotations

MAX_PLANS = 5
MAX_WATCH = 8
MIN_RR = 2.0          # below this a plan is flagged: the reward doesn't pay for the risk
TIMING_OK = 4         # intraday rules (of 5) that make it a reasonable moment to enter
GOOD_SECTORS = ("favour", "accumulate")
BAD_SECTORS = ("caution", "avoid")

GATES = {
    "strong": ("Market supports new buys", "bullish", True),
    "neutral": ("No tailwind from the market", "neutral", True),
    "weak": ("Weak market: no new buys", "bearish", False),
    "unknown": ("Market mood unknown", "neutral", True),
}


def gate(mood: dict | None) -> dict:
    """Step 1. `go` is False only in a weak market: most stocks fall with a falling market."""
    if mood is None:  # no EGX stock in the list: the mood is only measured for the EGX
        return {"key": "unknown", "label": "No market mood for this list", "tone": "neutral", "go": True,
                "text": "Market mood is measured for the EGX only.", "advice": "Judge each stock on its own."}
    key = mood.get("key") or "unknown"
    label, tone, go = GATES.get(key, GATES["unknown"])
    advice = {"strong": "Go ahead with the best setups.",
              "neutral": "Be picky: only plans with a clear reward for the risk, and keep sizes small.",
              "weak": "Manage what you hold; leave new buys until the market turns.",
              "unknown": "The market snapshot didn't load, so judge each stock without it."}.get(key, "")
    return {"key": key, "label": label, "tone": tone, "go": go, "text": (mood or {}).get("text") or "No market data.", "advice": advice}


def sector_view(sectors: list[dict] | None) -> dict | None:
    """Step 2: the sectors to prefer and the ones to be wary of. None when the outlook didn't load."""
    if sectors is None:
        return None
    pick = lambda keys: [{"sector": s["sector"], "slug": s["slug"], "label": s["conclusion"]["label"]}
                         for s in sectors if s["conclusion"]["verdict"] in keys]
    return {"prefer": pick(GOOD_SECTORS), "wary": pick(BAD_SECTORS)}


def from_yahoo(symbol: str) -> bool:
    """An EGX stock answered by the fallback source: its history isn't always adjusted for splits and bonus shares."""
    return symbol.upper().endswith(".CA")


def timing_view(row: dict | None) -> dict | None:
    """Step 5 for one stock, from an intraday scan row."""
    if not row or "error" in row:
        return None
    ok = row["passed"] >= TIMING_OK
    return {"passed": row["passed"], "lastBar": row["lastBar"], "vwapPct": row.get("vwapPct"), "rvol": row.get("rvol"), "ok": ok,
            "checks": [{"label": c["label"], "ok": c["ok"], "text": c["text"]} for c in row["checks"]],
            "advice": "A reasonable moment to enter." if ok else "Wait for a better moment, or another day."}


def plan(call: dict, timing: dict | None = None) -> dict:
    """Step 4: one buy call (a final.buy_list row) as a trade plan."""
    lv, sz = call.get("levels") or {}, call.get("sizing") or {}
    checks = call.get("checks") or []
    shares = sz.get("shares") if sz.get("ok") else None
    entry, stop = lv.get("entry"), lv.get("stop")
    pros = [f"{e['source']}: {e['reading']}" for e in call["evidence"] if e["stance"] == "for"]
    pros += [c["label"] for c in checks if c["status"] == "pass" and c["key"] in ("trend", "growth", "quality", "sector", "liquidity")]
    cons = [{"label": f"{e['source']}: {e['reading']}", "detail": e.get("detail", "")} for e in call["evidence"] if e["stance"] == "against"]
    cons += [{"label": c["label"], "detail": c["detail"]} for c in checks if c["status"] in ("warn", "fail")]
    blind = [c["label"] for c in checks if c["status"] == "unknown"]

    flags = []
    if from_yahoo(call["symbol"]):
        flags.append({"key": "source", "text": "Prices came from Yahoo, the fallback source. Its history isn't always adjusted for splits "
                      "and bonus shares, which can misplace the 200-day average and flip the call. Check again in a few minutes."})
    if lv.get("rr") is not None and lv["rr"] < MIN_RR:
        flags.append({"key": "rr", "text": f"Reward is only {lv['rr']:.1f} times the risk: aim for {MIN_RR:.0f} or more, or wait for a better entry."})
    if not shares:
        flags.append({"key": "size", "text": sz.get("reason") or sz.get("limitNote") or "Your money rules leave no room for this buy."})
    if len(blind) >= 3:
        flags.append({"key": "data", "text": f"{len(blind)} checks have no data ({', '.join(blind)}): the call rests on less than usual."})

    halal = call.get("halal")
    return {
        "symbol": call["symbol"], "name": call["name"], "last": call["last"], "changePct": call["changePct"], "lastBar": call.get("lastBar"),
        "call": call["decision"]["label"], "callKey": call["decision"]["key"], "reason": call["decision"]["reason"],
        "held": call.get("position"),
        "shares": shares, "entry": entry, "stop": stop, "target": lv.get("target"), "rr": lv.get("rr"), "upsidePct": lv.get("upsidePct"),
        "levelsFrom": lv.get("source"),
        "stopPct": round((stop / entry - 1) * 100, 2) if entry and stop else None,
        "cost": sz.get("cost") if shares else None, "risk": sz.get("risk") if shares else None,
        "riskPct": sz.get("riskPct") if shares else None, "limitedBy": sz.get("limitNote") if shares else None,
        "for": pros, "against": cons, "noData": blind, "flags": flags,
        "sectorOk": any(c["key"] == "sector" and c["status"] == "pass" for c in checks),
        "halal": halal, "timing": timing, "ticket": call.get("ticket"),
        "clean": not any(f["key"] in ("source", "rr", "size") for f in flags),
    }


def is_halal(row: dict) -> bool:
    return (row.get("halal") or {}).get("status") == "halal"


def build(buys: dict, sectors: list[dict] | None = None, timings: dict[str, dict] | None = None, halal_only: bool = False) -> dict:
    """The whole routine from a final.buy_list answer (rows tagged with `halal`), the sector outlook and
    intraday rows keyed by symbol."""
    g = gate(buys.get("mood"))
    calls = [c for c in buys["results"] if not halal_only or is_halal(c)]
    plans = [plan(c, timing_view((timings or {}).get(c["symbol"]))) for c in calls[:MAX_PLANS]]
    # nothing shaky first, then stocks in a favoured sector; the buy list's own order (strongest call first) breaks ties
    plans = [p for _, p in sorted(enumerate(plans), key=lambda ip: (not ip[1]["clean"], not ip[1]["sectorOk"], ip[0]))]
    watch = [w for w in buys.get("watch", []) if not halal_only or is_halal(w)][:MAX_WATCH]

    names = ", ".join(p["symbol"] for p in plans)
    if not g["go"]:
        outcome = {"key": "weak", "tone": "bearish", "title": "No new buys today",
                   "text": f"The market is weak. {g['text']}" + (f" {names} would otherwise qualify." if plans else "")}
    elif not plans:
        left_out = len(buys["results"]) - len(calls)
        outcome = {"key": "none", "tone": "neutral", "title": "No trade today",
                   "text": f"None of the {buys['checked']} stocks checked is a buy right now"
                           + (f" ({left_out} left out by Halal only)." if left_out else ".")
                           + " That is a result, not a failure: the watch list shows what is closest."}
    else:
        clean = [p["symbol"] for p in plans if p["clean"]]
        outcome = {"key": "trade", "tone": "bullish" if clean else "neutral",
                   "title": f"{len(plans)} stock{'s' if len(plans) > 1 else ''} to consider: {names}",
                   "text": (f"Cleanest: {', '.join(clean)}." if clean and len(clean) < len(plans) else
                            "Each passes the buy checklist; read what speaks against it before buying." if clean else
                            "Each has a flag to clear first: see below.")}
    return {
        "outcome": outcome,
        "market": g,
        "sectors": sector_view(sectors),
        "shortlist": {"checked": buys["checked"], "counts": buys["counts"], "skipped": buys.get("skipped", []),
                      "buys": len(buys["results"]), "shown": len(plans)},
        "plans": plans,
        "watch": watch,
        "asOf": buys.get("asOf"),
        "note": "The buy checklist and the final call are discipline, not a tested edge. Not financial advice.",
    }
