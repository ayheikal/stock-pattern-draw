"""Buy checklist: one Ready / Wait / Avoid verdict per stock, from the facts that matter for a decision.

The chart outlook is deliberately not a check: the Track record shows it has no reliable edge, so it is
shown beside the checklist as information only. Each check is pass / warn / fail / unknown, and unknown
never counts as a pass. Rules:
  - any fail                          -> Avoid
  - 3+ of the core checks unknown     -> Not enough data
  - a check that says "wait" (results due soon, stale data, no room in my rules), or 2+ warnings -> Wait
  - otherwise                         -> Ready to buy
Fundamentals come from TradingView's EGX screener, and from Yahoo Finance for stocks outside the EGX (which have no
sector median, so no P/E-vs-sector check). The fundamental + technical rating (rating.py) is attached to the result,
and its fundamental score is one of the checks.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import rating as ratings

CORE = ("growth", "quality", "valuation", "sector", "trend", "rr")
MAX_UNKNOWN_CORE = 2      # more core checks unknown than this: not enough data to call it
MAX_WARNINGS = 1          # more warnings than this: wait
RR_GOOD, RR_OK = 2.0, 1.0  # reward / risk: pass at 2+, warn from 1, fail below
PE_PRICEY = 2.0           # P/E above this multiple of the sector median fails; above the median warns
STALE_SESSIONS = 3        # this many missed sessions since the last bar: the data is stale
EGX_DAYS = {6, 0, 1, 2, 3}  # Sun-Thu (Python: Mon=0 .. Sun=6)
WEEK_DAYS = {0, 1, 2, 3, 4}
GOOD_SECTORS = {"favour", "accumulate"}
SQUEEZED = {"cash", "total risk", "sector"}  # sizing limits that come from what I already hold

VERDICTS = {
    "ready": ("Ready to buy", 0),
    "wait": ("Wait", 1),
    "unknown": ("Not enough data", 2),
    "avoid": ("Avoid", 3),
}


def _check(key: str, label: str, status: str, detail: str, wait: bool = False) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "wait": wait}


def is_egx(symbol: str, currency: str | None = None) -> bool:
    return symbol.endswith(".CA") or symbol.startswith("EGX:") or currency == "EGP"


def yahoo_stock(symbol: str) -> bool:
    """Could be a stock Yahoo has fundamentals for: not EGX, not an index (^GSPC), FX (EURUSD=X) or TradingView code."""
    return not is_egx(symbol) and not symbol.startswith("^") and not any(c in symbol for c in "=:")


def sessions_missed(last: date, today: date, egx: bool, holidays=()) -> int:
    """Trading sessions between the last bar and today (both excluded), so 0 means the data is up to date."""
    days, hol = (EGX_DAYS if egx else WEEK_DAYS), set(holidays)
    n, d = 0, last + timedelta(days=1)
    while d < today and n < 99:
        n += d.weekday() in days and d not in hol
        d += timedelta(days=1)
    return n


def earnings_date(ts: float | None) -> date | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None


# --- the checks -----------------------------------------------------------------------------------

def check_halal(halal: dict | None) -> dict:
    label = "Halal"
    if not halal:
        return _check("halal", label, "unknown", "Not on your EGX 33 Shariah list and not tagged by you. "
                      "The index holds only the most liquid compliant stocks, so this doesn't make it non-compliant.")
    if halal["status"] == "halal":
        return _check("halal", label, "pass", "Your own halal tag." if halal["source"] == "mine" else "In the EGX 33 Shariah list.")
    return _check("halal", label, "warn", "You tagged it not halal.")


def check_growth(fund: dict | None, inflation: float) -> dict:
    label = "Growth beats inflation"
    eps, rev = (fund or {}).get("epsGrowth"), (fund or {}).get("revenueGrowth")
    known = [(n, v) for n, v in (("Earnings per share", eps), ("Revenue", rev)) if v is not None]
    if not known:
        return _check("growth", label, "unknown", "No growth figures reported.")
    text = "; ".join(f"{n} {v:+.0f}% ({v - inflation:+.0f}% after {inflation:g}% inflation)" for n, v in known)
    beats = sum(v > inflation for _, v in known)
    if beats == len(known):
        return _check("growth", label, "pass", text + ".")
    if eps is not None and eps < 0 and (rev is None or rev <= inflation):
        return _check("growth", label, "fail", text + ". Earnings are shrinking.")
    return _check("growth", label, "warn", text + ".")


def check_quality(fund: dict | None, inflation: float) -> dict:
    label = "Profitable (return on equity)"
    roe, margin = (fund or {}).get("roe"), (fund or {}).get("netMargin")
    if roe is None:
        return _check("quality", label, "unknown", "Return on equity not reported.")
    text = f"ROE {roe:.0f}%" + (f", net margin {margin:.0f}%" if margin is not None else "")
    if roe <= 0:
        return _check("quality", label, "fail", text + ": the company is losing money for its owners.")
    if roe < inflation:
        return _check("quality", label, "warn", text + f": below {inflation:g}% inflation, so owners lose in real terms.")
    return _check("quality", label, "pass", text + f": above {inflation:g}% inflation.")


def check_valuation(fund: dict | None, sector: dict | None) -> dict:
    label = "Price vs sector (P/E)"
    pe, median = (fund or {}).get("pe"), (sector or {}).get("pe")
    if pe is None:
        return _check("valuation", label, "unknown", "No P/E (loss-making, or not reported).")
    if pe <= 0:
        return _check("valuation", label, "fail", f"P/E {pe:.1f}: no earnings to pay for.")
    if not median or median <= 0:
        return _check("valuation", label, "unknown", f"P/E {pe:.1f}; no sector median to compare with.")
    text = f"P/E {pe:.1f} vs {median:.1f} for the median {sector['sector']} company"
    if pe <= median:
        return _check("valuation", label, "pass", text + ": not expensive.")
    if pe <= median * PE_PRICEY:
        return _check("valuation", label, "warn", text + ": pricier than its sector.")
    return _check("valuation", label, "fail", text + f": more than {PE_PRICEY:g}× its sector.")


def check_fundamentals(rating: dict | None) -> dict:
    """The fundamental scorecard (valuation, profitability, growth, debt, liquidity, cash flow) as one check."""
    label = "Fundamental score"
    f = (rating or {}).get("fundamental")
    if not f or f["grade"] == "unknown":
        n = f["scored"] if f else 0
        return _check("fundamentals", label, "unknown", f"Only {n} of the scorecard's figures are reported: too few to score.")
    weakest = [c["label"].lower() for c in sorted(f["categories"], key=lambda c: c["pct"] if c["pct"] is not None else 101)
               if c["pct"] is not None and c["pct"] < ratings.FUND_FAIR][:2]
    text = f"{f['pct']}% across {f['scored']} figures"
    if f["grade"] == "strong":
        return _check("fundamentals", label, "pass", text + ": a strong business on most measures.")
    if f["grade"] == "fair":
        return _check("fundamentals", label, "warn", text + ": average." + (f" Weakest: {', '.join(weakest)}." if weakest else ""))
    return _check("fundamentals", label, "fail", text + ": most figures fall short." + (f" Weakest: {', '.join(weakest)}." if weakest else ""))


def check_sector(sector: dict | None) -> dict:
    label = "Sector outlook"
    if not sector or not sector.get("verdict"):
        return _check("sector", label, "unknown", "No sector outlook for this stock.")
    text = f"{sector['sector']}: {sector['label']}."
    v = sector["verdict"]
    return _check("sector", label, "pass" if v in GOOD_SECTORS else "fail" if v == "avoid" else "warn", text)


def check_trend(row: dict) -> dict:
    label = "Above its 200-day average"
    above = row.get("aboveSma200")
    if above is None:
        return _check("trend", label, "unknown", "Less than 200 bars of history.")
    if above:
        return _check("trend", label, "pass", "The long-term trend is up.")
    return _check("trend", label, "fail", "Below its 200-day average: don't buy into a long-term downtrend.")


def check_liquidity(row: dict, min_adtv: float) -> dict:
    label = "Liquid enough"
    v = row.get("adtv")
    if v is None:
        return _check("liquidity", label, "unknown", "No traded value for this symbol.")
    if v >= min_adtv:
        return _check("liquidity", label, "pass", f"Trades about {v:,.0f} a day.")
    return _check("liquidity", label, "warn", f"Thin: about {v:,.0f} a day (your minimum {min_adtv:,.0f}). Hard to sell quickly.")


def check_earnings(fund: dict | None, today: date, blackout_days: int) -> dict:
    label = "No results due soon"
    d = earnings_date((fund or {}).get("nextEarnings"))
    if d is None or d < today:
        return _check("earnings", label, "unknown", "No upcoming results date reported.")
    days = (d - today).days
    if days <= blackout_days:
        return _check("earnings", label, "warn", f"Results due on {d.isoformat()} (in {days} day{'s' if days != 1 else ''}): "
                      "a surprise can gap the price past your stop. Wait until they're out.", wait=True)
    return _check("earnings", label, "pass", f"Next results on {d.isoformat()}.")


def check_fresh(row: dict, today: date, interval: str, holidays=()) -> dict:
    label = "Data up to date"
    last = row.get("lastBar")
    if not last:
        return _check("fresh", label, "unknown", "No date for the last price.")
    last = date.fromisoformat(last)
    if interval == "1wk":
        stale = (today - last).days > 14
    else:
        stale = sessions_missed(last, today, is_egx(row.get("symbol", ""), row.get("currency")), holidays) >= STALE_SESSIONS
    if stale:
        return _check("fresh", label, "warn", f"Last price is from {last.isoformat()}: the stock may be suspended, "
                      "or the data feed is behind. Check before acting.", wait=True)
    return _check("fresh", label, "pass", f"Last price from {last.isoformat()}.")


def check_rr(plan: dict | None) -> dict:
    label = "Reward vs risk"
    if not plan or not plan.get("stop") or not plan.get("target"):
        return _check("rr", label, "unknown", "Needs a target and a stop.")
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    whose = {"yours": "your", "chart": "the chart's"}.get(plan.get("source"), "your / the chart's")
    if stop >= entry:
        return _check("rr", label, "unknown", f"The stop ({stop:,.2f}) is not below the price.")
    if target <= entry:
        return _check("rr", label, "fail", f"Already at {whose} target ({target:,.2f}): no upside left.")
    rr = (target - entry) / (entry - stop)
    text = f"{rr:.1f} : 1 with {whose} target {target:,.2f} and stop {stop:,.2f}"
    if rr >= RR_GOOD:
        return _check("rr", label, "pass", text + ".")
    if rr >= RR_OK:
        return _check("rr", label, "warn", text + f": aim for {RR_GOOD:g} or more.")
    if plan.get("source") == "chart":  # the chart's levels have no proven edge either: they can only warn
        return _check("rr", label, "warn", text + ". Set your own target and stop to judge it.")
    return _check("rr", label, "fail", text + ": the stop risks more than the target gains.")


def check_fit(sizing: dict | None) -> dict:
    """From profile.sizing_for: does a position fit my money rules and what's left of my portfolio?"""
    label = "Fits my money rules"
    if not sizing or sizing.get("configured") is False:
        return _check("fit", label, "unknown", "Set your capital in My profile to check this.")
    if not sizing.get("ok"):
        if not sizing.get("limitedBy"):  # e.g. capital in another currency, or no usable stop: not a verdict on the stock
            return _check("fit", label, "unknown", sizing.get("reason", "Can't be sized."))
        return _check("fit", label, "warn", sizing["reason"], wait=True)
    text = f"Up to {sizing['shares']:,} shares, risking {sizing['riskPct']:g}% of your capital."
    if sizing.get("limitedBy") in SQUEEZED:
        return _check("fit", label, "warn", f"{text} Reduced, because {sizing['limitNote']}")
    if sizing.get("limitNote"):
        return _check("fit", label, "pass", f"{text} Capped: {sizing['limitNote']}")
    return _check("fit", label, "pass", text)


# --- verdict --------------------------------------------------------------------------------------

def verdict(checks: list[dict]) -> dict:
    by = {s: [c for c in checks if c["status"] == s] for s in ("pass", "warn", "fail", "unknown")}
    unknown_core = [c for c in by["unknown"] if c["key"] in CORE]
    if by["fail"]:
        key, reason = "avoid", by["fail"][0]["detail"]
    elif len(unknown_core) > MAX_UNKNOWN_CORE:
        key, reason = "unknown", "Missing: " + ", ".join(c["label"].lower() for c in unknown_core) + "."
    elif any(c["wait"] for c in checks):
        key, reason = "wait", next(c for c in checks if c["wait"])["detail"]
    elif len(by["warn"]) > MAX_WARNINGS:
        key = "wait"
        reason = f"{len(by['warn'])} cautions: " + " · ".join(f"{c['label']}: {c['detail']}" for c in by["warn"])
    else:
        key = "ready"
        reason = (f"Passes, with one caution. {by['warn'][0]['label']}: {by['warn'][0]['detail']}" if by["warn"]
                  else "Every check that has data passes.")
    label, rank = VERDICTS[key]
    known = len(checks) - len(by["unknown"])
    return {"verdict": key, "label": label, "rank": rank, "reason": reason,
            "passed": len(by["pass"]), "known": known, "total": len(checks),
            "counts": {s: len(v) for s, v in by.items()}}


def checklist(row: dict, *, fund: dict | None, sector: dict | None, halal: dict | None, settings, inflation_pct: float,
              plan: dict | None, sizing: dict | None, today: date, interval: str = "1d", rating: dict | None = None) -> dict:
    """The checks and the verdict for one stock. `row` is a screener scan row; `settings` the profile's;
    `rating` the fundamental + technical rating (rating.rate), shown alongside and scored as one check."""
    if not row.get("currency"):
        return {"applicable": False, "verdict": "index", "label": "Index", "rank": 9,
                "reason": "An index can't be bought directly; use a fund that tracks it.", "checks": []}
    checks = [
        check_trend(row),
        check_growth(fund, inflation_pct),
        check_quality(fund, inflation_pct),
        check_valuation(fund, sector),
        check_fundamentals(rating),
        check_sector(sector),
        check_rr(plan),
        check_liquidity(row, settings.minAdtv),
        check_earnings(fund, today, settings.earningsBlackoutDays),
        check_fresh(row, today, interval, settings.holidays),
        check_fit(sizing),
        check_halal(halal),
    ]
    out = {"applicable": True, "checks": checks} | verdict(checks)
    if plan and plan["stop"] < plan["entry"]:  # the levels the reward/risk check judged, for tables to show
        out["plan"] = plan | {"rr": round((plan["target"] - plan["entry"]) / (plan["entry"] - plan["stop"]), 2),
                              "upsidePct": round((plan["target"] / plan["entry"] - 1) * 100, 2)}
    d = earnings_date((fund or {}).get("nextEarnings"))
    out["nextEarnings"] = d.isoformat() if d and d >= today else None
    out["chart"] = {"bias": row.get("bias"), "score": row.get("score"),
                    "note": "Chart reading only: the Track record shows no reliable edge, so it isn't a check."}
    out["rating"] = rating
    return out


def plan_for(row: dict, entry=None) -> dict | None:
    """Levels for the reward/risk check: my own target and stop when set (and sensible), else the chart's."""
    last = row.get("last")
    if not last:
        return None
    mine_stop = entry.stop if entry is not None and entry.stop and entry.stop < last else None
    mine_target = entry.target if entry is not None and entry.target else None
    stop, target = mine_stop or row.get("chartStop"), mine_target or row.get("upTarget")
    if not stop or not target:
        return None
    source = "yours" if mine_stop and mine_target else "chart" if not (mine_stop or mine_target) else "mixed"
    return {"entry": last, "stop": stop, "target": target, "source": source}


def sector_index(funds: list[dict], inflation_pct: float, views: dict, today: date) -> dict[str, dict]:
    """{sector name: {"sector", "pe", "verdict", "label"}} from the Sectors page's own outlook."""
    from .egx import sector_outlook
    out = {}
    for s in sector_outlook(funds, inflation_pct, views, today)["sectors"]:
        out[s["sector"]] = {"sector": s["sector"], "slug": s["slug"], "pe": s["pe"],
                            "verdict": s["conclusion"]["verdict"], "label": s["conclusion"]["label"]}
    return out


class Context:
    """Everything the checklist needs that isn't per symbol, fetched once: fundamentals, sectors, my portfolio.
    Each part degrades to "unknown" when its source is offline."""

    def __init__(self, prof, today: date | None = None, book: dict | None = None):
        from . import profile
        from .egx import fetch_fundamentals, fetch_market
        self.prof, self.today = prof, today or date.today()
        try:
            funds = fetch_fundamentals()["stocks"]
        except Exception:
            funds = []
        self.funds = {f["symbol"]: f for f in funds}
        self.medians = ratings.sector_medians(funds)
        try:
            self.sectors = sector_index(funds, prof.inflationPct, prof.sectors, self.today) if funds else {}
        except Exception:
            self.sectors = {}
        if book is None:
            try:
                market = fetch_market()["stocks"]
            except Exception:
                market = []
            book = profile.light_book(prof, market)
        self.book = book
        self.entries = {e.symbol: e for e in prof.entries}

    def fund(self, symbol: str) -> dict | None:
        """TradingView's figures for an EGX stock; Yahoo's for any other stock."""
        from .shariah import key
        f = self.funds.get(key(symbol))
        if f is None and yahoo_stock(symbol):
            f = ratings.fetch_yahoo_fundamentals(symbol)
        return f

    def prefetch(self, symbols) -> None:
        """Fetch Yahoo fundamentals for many symbols at once, so a scan doesn't wait for them one by one."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [s for s in dict.fromkeys(symbols) if s and yahoo_stock(s)]
        if todo:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(ratings.fetch_yahoo_fundamentals, todo))

    def sector(self, symbol: str) -> dict | None:
        """The EGX sector outlook; Yahoo's sector names don't match EGX sectors, so other stocks have none."""
        f = self.fund(symbol)
        return self.sectors.get(f["sector"]) if f and f.get("sector") and f.get("source") != "yahoo" else None

    def rating(self, row: dict) -> dict | None:
        """The fundamental + technical rating for a scan row (None for indices)."""
        if not row.get("currency"):
            return None
        f = self.fund(row["symbol"])
        medians = self.medians.get(f["sector"]) if f and f.get("sector") and f.get("source") != "yahoo" else None
        return ratings.rate(f, medians, ratings.inflation_for(row["currency"], self.prof.inflationPct), row.get("tech"))

    def entry(self, row: dict):
        from .shariah import key
        for s in (row.get("query"), row.get("symbol")):
            if s and s.upper() in self.entries:
                return self.entries[s.upper()]
        k = key(row.get("symbol", ""))
        return next((e for sym, e in self.entries.items() if key(sym) == k), None)

    def evaluate(self, row: dict, interval: str = "1d", entry=None, sizing: dict | None = None) -> dict:
        from . import profile, shariah
        entry = entry if entry is not None else self.entry(row)
        plan = plan_for(row, entry)
        if sizing is None and row.get("currency") and row.get("last"):
            held = entry is not None and entry.position == "long" and (entry.qty or entry.trades)
            sizing = None if held else profile.sizing_for(
                self.prof.settings, row["currency"], row["last"], plan["stop"] if plan else row.get("chartStop"),
                row.get("adtv"), book=self.book, sector=(self.fund(row["symbol"]) or {}).get("sector"))
        return checklist(row, fund=self.fund(row["symbol"]), sector=self.sector(row["symbol"]),
                         halal=shariah.status(self.prof.shariah, row["symbol"]), settings=self.prof.settings,
                         inflation_pct=ratings.inflation_for(row.get("currency"), self.prof.inflationPct),
                         plan=plan, sizing=sizing, today=self.today, interval=interval, rating=self.rating(row))
