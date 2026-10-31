"""Final decision: one call per stock (Buy, Wait, Don't buy, Hold, Reduce, Sell) from every analysis in the app.

Each page answers one question. This page weighs the answers by how much they can be trusted:
  decides   My plan (My profile)  a held stock's stop, target, view and size rules: the risk I chose comes first
            Buy checklist         the gate for any buy (trend, growth, ROE, P/E vs sector, fundamentals, sector outlook,
                                  reward/risk, liquidity, results dates, fresh data, my money rules)
  weighs    Rating                fundamentals × trend
            Fair value            cheap or dear against its peers' multiples (EGX only)
            Market mood           EGX 30's trend and the share of EGX stocks above their 200-day average
  context   Chart outlook         with its track record on this stock: the Track record shows no reliable edge, so no vote
            Halal                 a flag, except my own "not halal" tag, which blocks a buy

A stock I don't hold:
  the checklist says Avoid, the rating says Sell, or I tagged it not halal  -> Don't buy
  the checklist says Not enough data / Wait                                -> the same
  the checklist says Ready: each weighing input for +1, against −1; 2+ -> Strong buy, 0..1 -> Buy, below 0 -> Wait
A stock I hold:
  my plan says sell (stop or target hit, bearish view, too big)            -> what My profile says (Cut loss, Trim, ...)
  the rating says Sell                                                     -> Sell
  2+ against among the checklist, rating, fair value, market and halal      -> Reduce
  my plan says Add more and nothing is against                             -> Add more; otherwise Hold
Fundamentals have no history to backtest, so this is discipline, not a forecast.
"""
from __future__ import annotations

from . import backtest, decision, profile, shariah, valuation
from .screener import _analyze_one

STRONG_BUY = 2          # this many more inputs for than against, on a Ready checklist: Strong buy
REDUCE_AGAINST = 2      # a held stock with this many inputs against it: Reduce
BREADTH_OK = 50.0       # % of EGX stocks above their 200-day average for a healthy market

# key -> label, tone, group (the plain Buy / Hold / Wait / Sell / Avoid)
DECISIONS = {
    "strong-buy": ("Strong buy", "bullish", "Buy"),
    "buy": ("Buy", "bullish", "Buy"),
    "add": ("Add more", "bullish", "Buy"),
    "hold": ("Hold", "neutral", "Hold"),
    "wait": ("Wait", "neutral", "Wait"),
    "unknown": ("Not enough data", "neutral", "Wait"),
    "index": ("Not a stock", "neutral", "Wait"),
    "dont-buy": ("Don't buy", "bearish", "Avoid"),
    "reduce": ("Reduce", "bearish", "Sell"),
    "sell": ("Sell", "bearish", "Sell"),
}
PAGES = {"plan": "/profile", "checklist": "/", "rating": "/", "value": "/value", "market": "/market", "chart": "/track",
         "halal": "/profile"}


def _ev(key: str, source: str, reading: str, detail: str, stance: str, role: str, veto: bool = False, **extra) -> dict:
    """One input: `stance` for / against / neutral / unknown; `role` decides / weighs / context."""
    return {"key": key, "source": source, "page": PAGES[key], "reading": reading, "detail": detail,
            "stance": stance, "role": role, "veto": veto} | extra


# --- the inputs -----------------------------------------------------------------------------------

def ev_plan(card: dict | None, held: bool) -> dict:
    """My profile's own action for this stock (profile.compare)."""
    if card is None:
        return _ev("plan", "My plan", "Not in My profile", "Add it to My profile to set your own view, stop and target.",
                   "unknown", "context")
    a = card["action"]
    stance = {"Buy": "for", "Sell": "against"}.get(a["group"], "neutral")
    return _ev("plan", "My plan", a["action"], a["reason"], stance, "decides" if held else "context", action=a)


def ev_checklist(ck: dict) -> dict:
    if not ck.get("applicable"):
        return _ev("checklist", "Buy checklist", ck["label"], ck["reason"], "unknown", "decides")
    stance = {"ready": "for", "avoid": "against", "unknown": "unknown"}.get(ck["verdict"], "neutral")
    return _ev("checklist", "Buy checklist", ck["label"], f"{ck['passed']} of {ck['known']} checks with data pass. {ck['reason']}",
               stance, "decides", veto=ck["verdict"] == "avoid")


def ev_rating(rating: dict | None) -> dict:
    sig = (rating or {}).get("signal")
    if not sig or sig["key"] == "unknown":
        return _ev("rating", "Rating", "Not enough data", "Too few financial figures and too little price history.", "unknown", "weighs")
    stance = {"strong-buy": "for", "buy": "for", "reduce": "against", "sell": "against"}.get(sig["key"], "neutral")
    f, t = rating["fundamental"], rating.get("technical") or {}
    detail = f"Fundamentals {f['label'].lower()}, trend {t.get('label', 'unknown').lower()}. {sig['text']}"
    return _ev("rating", "Rating", sig["label"], " ".join([detail] + sig["notes"]), stance, "weighs", veto=sig["key"] == "sell")


def ev_value(look: dict | None) -> dict:
    """valuation.lookup's answer; None for a stock outside the EGX."""
    if look is None:
        return _ev("value", "Fair value", "Not valued", "Only EGX stocks are valued against their peers.", "unknown", "weighs")
    v = look["stock"]
    if v is None:
        return _ev("value", "Fair value", "Not valued", look["reason"], "unknown", "weighs")
    gap = f"{v['discountPct']:.0f}% under" if v["discountPct"] >= 0 else f"{-v['discountPct']:.0f}% above"
    detail = f"Price {v['price']:,.2f} is {gap} its fair value of {v['fair']:,.2f}. {v['text']}"
    # Cheap for a reason is still cheap; the checklist and rating already judge the weak business
    stance = "against" if v["verdict"] == "dear" else "neutral" if v["verdict"] in ("short", "trap") else "for"
    return _ev("value", "Fair value", v["label"], detail, stance, "weighs")


def market_mood(index: dict | None, breadth: dict | None) -> dict:
    """{"key": strong / neutral / weak / unknown, "text"} from EGX 30's trend and market breadth."""
    trend, above = (index or {}).get("trend"), (breadth or {}).get("above200")
    if trend not in ("Uptrend", "Downtrend", "Mixed") and above is None:
        return {"key": "unknown", "text": "No market data."}
    parts = [f"EGX 30 {trend.lower()}" if trend in ("Uptrend", "Downtrend", "Mixed") else None,
             f"{above:.0f}% of EGX stocks above their 200-day average" if above is not None else None]
    text = ", ".join(p for p in parts if p) + "."
    healthy = above is None or above >= BREADTH_OK
    if trend == "Uptrend" and healthy:
        return {"key": "strong", "text": text}
    if trend == "Downtrend" and not (above is not None and above >= BREADTH_OK):
        return {"key": "weak", "text": text + " Most stocks fall with a falling market: buy less, or wait."}
    return {"key": "neutral", "text": text}


def ev_market(mood: dict | None) -> dict:
    if mood is None:
        return _ev("market", "Market mood", "EGX only", "Market mood is measured for the EGX.", "unknown", "weighs")
    label = {"strong": "Strong", "weak": "Weak", "neutral": "Mixed", "unknown": "Unknown"}[mood["key"]]
    stance = {"strong": "for", "weak": "against", "unknown": "unknown"}.get(mood["key"], "neutral")
    return _ev("market", "Market mood", label, mood["text"], stance, "weighs")


def ev_chart(row: dict, track: dict | None) -> dict:
    line = profile._track_line(track, row.get("bias")) if track else None
    detail = "Not a vote: the Track record shows the chart outlook has no reliable edge."
    if line and None not in (line["asExpected"], line["avgRet"], line["baseline"]):
        detail += (f" On this stock, {line['bias'].lower()} readings went the expected way {line['asExpected']:g}% of "
                   f"{line['n']} times (average {line['avgRet']:+.1f}% vs {line['baseline']:+.1f}% for any period).")
    return _ev("chart", "Chart outlook", row.get("bias") or "—", detail, "neutral", "context")


def ev_halal(status: dict | None) -> dict:
    if not status:
        return _ev("halal", "Halal", "Unknown", "Not on your EGX 33 Shariah list and not tagged by you.", "unknown", "context")
    if status["status"] == "halal":
        return _ev("halal", "Halal", "Halal", "Your own tag." if status["source"] == "mine" else "In the EGX 33 Shariah list.",
                   "neutral", "context")
    return _ev("halal", "Halal", "Not halal", "You tagged it not halal.", "against", "decides", veto=True)


# --- the call -------------------------------------------------------------------------------------

def _call(key: str, reason: str, label: str | None = None) -> dict:
    lab, tone, group = DECISIONS[key]
    return {"key": key, "label": label or lab, "tone": tone, "group": group, "reason": reason}


def _names(evs: list[dict]) -> str:
    xs = [f"{e['source'].lower()} ({e['reading']})" for e in evs]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def decide(evidence: list[dict], checklist: dict, held: str | None) -> dict:
    """The final call. `held` is my position ("long", "short") or None; evidence from the ev_* functions."""
    ev = {e["key"]: e for e in evidence}
    plan = ev.get("plan", {}).get("action")
    weigh = [e for e in evidence if e["role"] == "weighs"]
    pros, cons = [e for e in weigh if e["stance"] == "for"], [e for e in weigh if e["stance"] == "against"]

    if held == "short":  # my own trade: only my plan speaks to it
        return _call("sell" if plan and plan["group"] == "Sell" else "hold", plan["reason"] if plan else "", plan and plan["action"])

    if held == "long":
        if plan and plan["group"] == "Sell":
            return _call("reduce" if plan["action"] == "Trim" else "sell", plan["reason"], plan["action"])
        if ev["rating"]["veto"]:
            return _call("sell", f"The rating says Sell: {ev['rating']['detail']}")
        against = cons + [e for e in (ev["checklist"], ev["halal"]) if e["stance"] == "against"]
        if len(against) >= REDUCE_AGAINST:
            return _call("reduce", f"{len(against)} things argue against holding it: {_names(against)}. "
                                   "Tighten your stop or trim.")
        if plan and plan["action"] == "Add more" and not against:
            return _call("add", plan["reason"])
        note = f" {plan['reason']}" if plan and plan["action"] in ("Set stop", "Watch closely") else ""
        if against:
            return _call("hold", f"Keep it, but {_names(against)} argues against adding.{note}")
        return _call("hold", f"Nothing argues for selling.{note}")

    if not checklist.get("applicable"):
        return _call("index", checklist["reason"])
    vetoes = [e for e in (ev["checklist"], ev["rating"], ev["halal"]) if e["veto"]]
    if vetoes:
        v = vetoes[0]
        return _call("dont-buy", f"{v['source']} says {v['reading']}: {v['detail'] if v['key'] != 'checklist' else checklist['reason']}")
    if checklist["verdict"] == "unknown":
        return _call("unknown", checklist["reason"])
    if checklist["verdict"] == "wait":
        return _call("wait", checklist["reason"])
    score = len(pros) - len(cons)
    if score >= STRONG_BUY:
        return _call("strong-buy", f"The buy checklist passes and {_names(pros)} agree." +
                     (f" Against: {_names(cons)}." if cons else ""))
    if score >= 0:
        return _call("buy", "The buy checklist passes." + (f" For: {_names(pros)}." if pros else "") +
                     (f" Against: {_names(cons)}." if cons else ""))
    return _call("wait", f"The buy checklist passes, but {_names(cons)} argue against buying now.")


# --- one stock, end to end ------------------------------------------------------------------------

class Shared:
    """What every stock's call needs, fetched once: my profile, fundamentals and sectors (decision.Context),
    and the EGX market mood. `index_card()` gives EGX 30's card (main._index_card)."""

    def __init__(self, index_card=None):
        self.prof = profile.load()
        self.ctx = decision.Context(self.prof)
        self._index_card, self._mood = index_card, None

    def mood(self) -> dict:
        if self._mood is None:
            from .egx import fetch_market, overview
            try:
                breadth = overview(fetch_market(), [])["breadth"]
            except Exception:
                breadth = None
            try:
                index = self._index_card() if self._index_card else None
            except Exception:
                index = None
            self._mood = market_mood(index, breadth)
        return self._mood


def final_decision(symbol: str, interval: str = "1d", index_card=None) -> dict:
    """Run every analysis for one symbol and combine them. Raises data.SymbolNotFound / InsufficientHistory like the chart."""
    row = _analyze_one(symbol, interval, "5y" if interval == "1wk" else "1y")
    return call_for(row, Shared(index_card), interval) | {"query": symbol}


def call_for(row: dict, shared: Shared, interval: str = "1d") -> dict:
    """The final call for one scan row (screener._analyze_one)."""
    prof, ctx = shared.prof, shared.ctx
    entry = ctx.entry(row)
    stock = bool(row.get("currency"))
    egx = stock and decision.is_egx(row["symbol"], row.get("currency"))
    track = backtest.track_for(row["symbol"], interval)

    card = sizing = None
    if entry is not None:
        card = profile.compare(entry, row, track, prof.settings, profile._actions_for(entry, row), None, ctx, interval)
        ck = card["decision"]
        held = card["position"] if card["position"] != "none" else None
        sizing = card.get("sizing")
    else:
        held = None
        if stock and row.get("last"):
            plan = decision.plan_for(row)
            sizing = profile.sizing_for(prof.settings, row["currency"], row["last"], plan["stop"] if plan else row.get("chartStop"),
                                        row.get("adtv"), book=ctx.book, sector=(ctx.fund(row["symbol"]) or {}).get("sector"))
        ck = ctx.evaluate(row, interval, sizing=sizing)

    look = mood = None
    if egx:
        look = (valuation.lookup(list(ctx.funds.values()), row["symbol"], prof.inflationPct) if ctx.funds
                else {"stock": None, "reason": "EGX company financials couldn't be loaded."})
        mood = shared.mood()

    halal = shariah.status(prof.shariah, row["symbol"]) if stock else None
    evidence = [ev_plan(card, held is not None), ev_checklist(ck)]
    if stock:
        evidence += [ev_rating(ck.get("rating")), ev_value(look), ev_market(mood)]
    evidence += [ev_chart(row, track)] + ([ev_halal(halal)] if stock else [])
    call = decide(evidence, ck, held) if stock else _call("index", ck["reason"])

    return {
        "symbol": row["symbol"], "query": row.get("query"), "name": row["name"], "currency": row.get("currency"),
        "last": row["last"], "changePct": row["changePct"], "lastBar": row.get("lastBar"), "interval": interval,
        "inProfile": entry is not None, "profileSymbol": entry.symbol if entry else None, "position": held,
        "holding": card and {k: card.get(k) for k in ("adjQty", "adjAvgCost", "plPct", "plValue", "stop", "target")},
        "decision": call, "evidence": evidence, "checks": ck.get("checks", []),
        "levels": ck.get("plan"), "sizing": sizing if call["group"] == "Buy" and not held else None,  # an add's shares are in the ticket
        "ticket": card and card.get("ticket"),
        "note": "Fundamentals can't be backtested, so this is discipline, not a forecast. Not financial advice.",
    }


# --- a whole watchlist: what to buy now -----------------------------------------------------------

BUY_RANK = {"strong-buy": 0, "add": 1, "buy": 2}


def buy_list(symbols: list[str], interval: str = "1d", index_card=None) -> dict:
    """Every stock in `symbols` whose final call is a buy (Strong buy, Add more, Buy): strongest call first, then more
    inputs for, then the more liquid. Each is sized on its own, as if it were the only new buy."""
    from .screener import scan
    scanned = scan(list(dict.fromkeys(symbols)), interval, "5y" if interval == "1wk" else "1y")
    rows = [r for r in scanned["results"] if r.get("currency")]
    shared = Shared(index_card)
    shared.ctx.prefetch(r["symbol"] for r in rows)
    calls = []
    for r in rows:
        try:
            calls.append(call_for(r, shared, interval) | {"adtv": r.get("adtv")})
        except Exception:  # one odd stock shouldn't sink the list
            scanned["skipped"].append(r["symbol"])
    counts = {}
    for c in calls:
        counts[c["decision"]["key"]] = counts.get(c["decision"]["key"], 0) + 1
    pros = lambda c: sum(e["stance"] == "for" for e in c["evidence"] if e["role"] == "weighs")
    buys = sorted((c for c in calls if c["decision"]["group"] == "Buy"),
                  key=lambda c: (BUY_RANK[c["decision"]["key"]], -pros(c), -(c["adtv"] or 0)))
    brief = lambda e: {k: e[k] for k in ("key", "source", "reading", "stance", "role")}
    return {
        "checked": len(calls), "skipped": scanned["skipped"], "counts": counts,
        "mood": shared._mood,
        "results": [{k: c[k] for k in ("symbol", "name", "currency", "last", "changePct", "position", "profileSymbol",
                                       "decision", "levels", "sizing", "ticket", "adtv")}
                    | {"evidence": [brief(e) for e in c["evidence"] if e["role"] != "context" or e["key"] == "halal"]}
                    for c in buys],
    }
