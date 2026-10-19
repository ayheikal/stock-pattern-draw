"""My profile: the symbols I follow, my position in each and what I expect from it.

Stored as JSON on disk (data/profile.json, or $PROFILE_PATH) and compared live against the chart outlook.
"""
from __future__ import annotations

import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from . import backtest, backup, decision, shariah
from .data import fetch_actions, fetch_ohlcv
from .screener import _scan_one
from .sizing import position_risk, size_position

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "profile.json"
_lock = threading.Lock()


class Trade(BaseModel):
    date: date
    side: Literal["buy", "sell"]
    qty: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fees: Optional[float] = Field(None, ge=0)  # from the contract note; estimated from my cost % when empty


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
    since: Optional[date] = None  # buy date: bonus shares and dividends after it are counted
    trades: list[Trade] = []  # when present, quantity / cost / dates come from these instead

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
    # Portfolio-wide limits: new buys are cut to fit what's left of them
    maxTotalRiskPct: float = Field(6.0, gt=0, le=50)  # all stops hit at once may cost at most this % of capital
    maxSectorPct: float = Field(35.0, gt=0, le=100)  # max in one EGX sector, % of capital
    earningsBlackoutDays: int = Field(10, ge=0, le=60)  # don't buy this close to a results date
    # Costs & taxes. Placeholders: rates differ by broker and change with the law, so check a contract note.
    feePct: float = Field(0.2, ge=0, le=5)  # per side: brokerage + exchange + clearing + stamp duty
    dividendTaxPct: float = Field(10.0, ge=0, le=100)
    gainsTaxPct: float = Field(0.0, ge=0, le=100)
    # Liquidity
    maxAdtvPct: float = Field(5.0, gt=0, le=100)  # never more than this share of a normal day's trading
    minAdtv: float = Field(1_000_000, ge=0)  # below this daily traded value a stock counts as thinly traded
    tickSize: float = Field(0.01, gt=0, le=10)  # price step for limit orders; check your broker's rules
    # Alerts
    alertsOn: bool = False
    telegramToken: str = Field("", max_length=200)
    telegramChatId: str = Field("", max_length=40)
    alertEveryMin: int = Field(15, ge=5, le=240)
    holidays: list[date] = []
    # Backup: an extra folder for a daily copy (e.g. inside OneDrive); data/backups is always used
    backupDir: str = Field("", max_length=300)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class SectorEvent(BaseModel):
    """Something coming up that should move a sector: a rate decision, a new law, a contract, ..."""
    date: date
    title: str = Field(..., min_length=1, max_length=120)
    impact: Literal["positive", "negative", "neutral"] = "neutral"


class SectorView(BaseModel):
    """My own view of a sector on the Sectors page; status None means "use the app's suggestion"."""
    status: Optional[Literal["growth", "normal", "shallow"]] = None
    note: str = Field("", max_length=1000)
    events: list[SectorEvent] = Field([], max_length=30)
    updated: Optional[date] = None


class Shariah(BaseModel):
    """Which stocks I count as halal: the official EGX 33 Shariah list (pasted in) and my own tags."""
    symbols: list[str] = Field([], max_length=300)  # list members, as COMI.CA
    listDate: Optional[date] = None  # when that list took effect
    tags: dict[str, Literal["halal", "not"]] = {}  # my own tags, keyed like COMI.CA; they win over the list


class Profile(BaseModel):
    name: str = Field("", max_length=60)
    settings: Settings = Settings()
    entries: list[Entry] = []
    sectors: dict[str, SectorView] = {}  # keyed by sector slug
    shariah: Shariah = Shariah()
    inflationPct: float = Field(12.0, ge=0, le=100)  # placeholder; the Sectors page judges real growth against it


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
    tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    backup.snapshot(p, profile.settings.backupDir)


def backups() -> dict:
    return backup.listing(_path())


def restore_backup(name: str) -> Profile:
    with _lock:
        backup.restore(_path(), name, Profile.model_validate)
        return load()


def set_name(name: str) -> Profile:
    with _lock:
        prof = load()
        prof.name = name.strip()
        save(prof)
    return prof


def set_settings(settings: Settings) -> Profile:
    with _lock:
        prof = load()
        if not settings.telegramToken:  # the page never sees the token, so empty means "keep it"
            settings.telegramToken = prof.settings.telegramToken
        elif settings.telegramToken.strip().lower() == "clear":
            settings.telegramToken = ""
        prof.settings = settings
        save(prof)
    return prof


def set_sector(slug: str, view: SectorView) -> Profile:
    """Save my view of a sector; an empty one (no status, note or events) removes it."""
    with _lock:
        prof = load()
        if view.status or view.note.strip() or view.events:
            view.note = view.note.strip()
            view.events = sorted(view.events, key=lambda e: e.date)
            view.updated = date.today()
            prof.sectors[slug] = view
        else:
            prof.sectors.pop(slug, None)
        save(prof)
    return prof


def set_inflation(pct: float) -> Profile:
    with _lock:
        prof = load()
        prof.inflationPct = pct
        save(prof)
    return prof


def set_shariah_list(symbols: list[str], list_date: date | None) -> Profile:
    with _lock:
        prof = load()
        prof.shariah.symbols = symbols
        prof.shariah.listDate = list_date if symbols else None
        save(prof)
    return prof


def set_halal_tag(symbol: str, status: Literal["halal", "not"] | None) -> Profile:
    """Tag a stock halal / not halal myself; None goes back to the list."""
    with _lock:
        prof = load()
        k = shariah.key(symbol)
        if status:
            prof.shariah.tags[k] = status
        else:
            prof.shariah.tags.pop(k, None)
        save(prof)
    return prof


def public_settings(settings: Settings) -> dict:
    """Settings as the page may see them: the Telegram token is replaced by a flag."""
    d = settings.model_dump(mode="json")
    d["hasTelegramToken"] = bool(d.pop("telegramToken"))
    return d


def public(prof: Profile) -> dict:
    return prof.model_dump(mode="json") | {"settings": public_settings(prof.settings)}


def sizing_for(settings: Settings, currency: str | None, entry: float, stop: float | None,
               adtv: float | None = None, book: dict | None = None, sector: str | None = None) -> dict:
    """How much to buy at `entry` with `stop`, under my rules; explains when it can't be sized.
    With `book` (my portfolio, see book_of) the buy also has to fit my cash, total risk and sector limit."""
    if not settings.capital:
        return {"ok": False, "configured": False, "reason": "Set your capital in My profile to see position sizes."}
    if not currency:
        return {"ok": False, "configured": True, "reason": "An index can't be bought directly (use a fund that tracks it)."}
    if currency.upper() != settings.currency:
        return {"ok": False, "configured": True, "reason": f"Your capital is in {settings.currency}; this is priced in {currency}."}
    if stop is None:
        return {"ok": False, "configured": True, "reason": "Needs a stop-loss price to size the position."}
    max_shares = int(adtv * settings.maxAdtvPct / 100 / entry) if adtv else None
    caps = portfolio_caps(settings, book, sector, entry, stop) if book and stop < entry else None
    return {"configured": True, "currency": settings.currency} | size_position(
        settings.capital, settings.riskPct, settings.maxPositionPct, entry, stop, settings.feePct, max_shares, caps)


def portfolio_caps(settings: Settings, book: dict, sector: str | None, entry: float, stop: float) -> dict[str, tuple[int, str]]:
    """Most extra shares the rest of my portfolio allows: cash left, total risk left, room left in the sector."""
    f, capital, cur = settings.feePct / 100, settings.capital, settings.currency
    per_buy = entry * (1 + f)
    per_risk = entry * (1 + f) - stop * (1 - f)
    cash = book["cash"]
    risk_room = capital * settings.maxTotalRiskPct / 100 - book["atRisk"]
    caps = {
        "cash": (math.floor(max(cash, 0) / per_buy), f"you have about {max(cash, 0):,.0f} {cur} in cash."),
        "total risk": (math.floor(max(risk_room, 0) / per_risk),
                       f"your positions already risk {book['atRisk'] / capital * 100:.1f}% of your capital if every stop is hit "
                       f"(your limit for all of them is {settings.maxTotalRiskPct:g}%)."),
    }
    if sector:
        in_sector = book["bySector"].get(sector, 0.0)
        room = math.floor(max(capital * settings.maxSectorPct / 100 - in_sector, 0) / per_buy)
        if in_sector:  # what I already hold there leaves less room
            caps["sector"] = (room, f"{sector} is already {in_sector / capital * 100:.0f}% of your capital "
                                    f"(your limit {settings.maxSectorPct:g}% per sector).")
        else:  # nothing there yet: the sector limit works like a size limit
            caps["sector size"] = (room, f"one sector may be at most {settings.maxSectorPct:g}% of your capital.")
    return caps


def _book(positions: list[dict], settings: Settings) -> dict:
    """My portfolio as the money rules see it. `positions`: {symbol, side, qty, last, stop, basis, realized, dividends, sector}.

    Capital is the money I put in, so cash = capital + realized gains + dividends - what open positions cost.
    """
    capital = settings.capital or 0.0
    cash, invested, at_risk, by_sector = capital, 0.0, 0.0, {}
    for p in positions:
        cash += p.get("realized", 0.0) + p.get("dividends", 0.0)
        if p["side"] == "long" and p["qty"]:
            cash -= p["basis"]
            value = p["qty"] * p["last"]
            invested += value
            if p.get("sector"):
                by_sector[p["sector"]] = by_sector.get(p["sector"], 0.0) + value
        if p["side"] != "none" and p["qty"] and p.get("stop") and capital:
            at_risk += position_risk(capital, p["side"], p["qty"], p["last"], p["stop"], settings.feePct)["risk"]
    return {"capital": capital, "currency": settings.currency, "cash": round(cash, 2), "invested": round(invested, 2),
            "atRisk": round(at_risk, 2), "bySector": {k: round(v, 2) for k, v in by_sector.items()}}


def book_of(entries: list[dict], settings: Settings, sector_of=lambda s: None) -> dict:
    """The book from live profile rows (compare() output), in my capital's currency only."""
    f = settings.feePct / 100
    positions = []
    for e in entries:
        if "error" in e or (e.get("currency") or "").upper() != settings.currency:
            continue
        j = e.get("journal")
        held = e["position"] != "none" and e.get("adjQty") and e.get("adjAvgCost")
        positions.append({
            "symbol": e["symbol"], "side": e["position"] if held else "none", "qty": e.get("adjQty") or 0.0,
            "last": e["last"], "stop": e.get("stop"),
            "basis": j["costBasis"] if j else (e["adjQty"] * e["adjAvgCost"] * (1 + f) if held else 0.0),
            "realized": j["realized"] if j else 0.0,
            "dividends": j["dividends"] if j else (e.get("income") or 0.0),
            "sector": sector_of(e.get("resolved") or e["symbol"]),
        })
    return _book(positions, settings)


def light_book(prof: Profile, market: list[dict]) -> dict:
    """The book without loading every chart: prices from the EGX market snapshot, no corporate actions.
    For the screener and chart page; My profile uses the full book_of."""
    f = prof.settings.feePct / 100
    snap = {s["symbol"]: s for s in market}
    positions = []
    for e in prof.entries:
        k = shariah.key(e.symbol)
        m = snap.get(k) or snap.get(k + ".CA")  # a bare EGX code (COMI) is saved as typed
        if m is None or not m.get("close") or prof.settings.currency != "EGP":
            continue
        if e.trades:
            j = ledger(e.trades, None, prof.settings)
            qty, basis, realized = j["qty"], j["costBasis"], j["realized"]
            side = "long" if qty > 0 else "none"
        else:
            side = e.position if e.qty and e.avgCost else "none"
            qty = e.qty or 0.0
            basis, realized = (qty * e.avgCost * (1 + f) if side == "long" else 0.0), 0.0
        positions.append({"symbol": e.symbol, "side": side, "qty": qty, "last": m["close"], "stop": e.stop,
                          "basis": basis, "realized": realized, "dividends": 0.0, "sector": m.get("sector")})
    return _book(positions, prof.settings)


def upsert(entry: Entry) -> Profile:
    with _lock:
        prof = load()
        old = next((e for e in prof.entries if e.symbol == entry.symbol), None)
        if old and not entry.trades:
            entry.trades = old.trades
        prof.entries = [e for e in prof.entries if e.symbol != entry.symbol] + [entry]
        save(prof)
    return prof


def add_trade(symbol: str, trade: Trade) -> Profile | None:
    with _lock:
        prof = load()
        e = next((e for e in prof.entries if e.symbol == symbol.strip().upper()), None)
        if e is None:
            return None
        e.trades = sorted(e.trades + [trade], key=lambda t: t.date)
        save(prof)
    return prof


def remove_trade(symbol: str, index: int) -> Profile | None:
    with _lock:
        prof = load()
        e = next((e for e in prof.entries if e.symbol == symbol.strip().upper()), None)
        if e is None or not 0 <= index < len(e.trades):
            return None
        del e.trades[index]
        save(prof)
    return prof


def ledger(trades: list[Trade], actions: list[dict] | None, settings: Settings, fx=None) -> dict:
    """Replay my trades with bonus shares, splits and dividends in date order (average-cost method).

    Same-day order: dividends and bonus shares first (they belong to whoever held before the ex-date),
    then my trades. Yahoo's dividends are per share after later splits, so each one is scaled back
    by the splits that came after it to get what was actually paid per share I held then.
    """
    f, dtax, gtax = settings.feePct / 100, settings.dividendTaxPct / 100, settings.gainsTaxPct / 100
    actions = actions or []
    later_splits = lambda d: math.prod(a["split"] for a in actions if a["date"] > d and a["split"] > 0)
    events = [(a["date"], 0, a) for a in actions] + [(t.date.isoformat(), 1, t) for t in trades]
    qty = cost = px = realized = fees = divs = cost_usd = 0.0  # cost includes buy fees; px is price only
    years: dict[str, dict] = {}
    applied, warnings = [], []
    first = trades[0].date.isoformat() if trades else None
    year = lambda d: years.setdefault(d[:4], {"year": d[:4], "realized": 0.0, "dividends": 0.0, "fees": 0.0})
    for d, _, ev in sorted(events, key=lambda x: (x[0], x[1])):
        if isinstance(ev, Trade):
            fee = ev.fees if ev.fees is not None else ev.qty * ev.price * f
            fees += fee
            year(d)["fees"] += fee
            if ev.side == "buy":
                qty += ev.qty
                cost += ev.qty * ev.price + fee
                px += ev.qty * ev.price
                if fx:
                    cost_usd += (ev.qty * ev.price + fee) / fx(d)
            else:
                q = min(ev.qty, qty)
                if q < ev.qty:
                    warnings.append(f"Sell of {ev.qty:g} on {d} is more than the {qty:g} held; counted as {q:g}.")
                if q <= 0:
                    continue
                basis = cost / qty * q
                gain = q * ev.price - fee - basis
                gain = gain * (1 - gtax) if gain > 0 else gain
                realized += gain
                year(d)["realized"] += gain
                cost -= basis
                px -= px / qty * q
                cost_usd -= cost_usd / qty * q
                qty -= q
                if qty < 1e-6:  # sold out: clear rounding dust
                    qty = cost = px = cost_usd = 0.0
        elif first and d > first and qty > 0:  # corporate action while I held shares
            if ev["split"] > 0:
                qty = round(qty * ev["split"], 6)
                applied.append({"date": d, "kind": "bonus" if ev["split"] > 1 else "split", "ratio": ev["split"]})
            if ev["dividend"] > 0:
                paid = ev["dividend"] * later_splits(d) * qty * (1 - dtax)
                divs += paid
                year(d)["dividends"] += paid
                applied.append({"date": d, "kind": "dividend", "perShare": round(ev["dividend"] * later_splits(d), 4)})
    rnd = lambda x: round(x, 2)
    return {
        "qty": round(qty, 4),
        # average price per share *excluding* buy costs (net P/L adds costs back); 0 shares -> closed
        "avgCost": round(px / qty, 4) if qty > 0 else None,
        "costBasis": rnd(cost),
        "costUsd": rnd(cost_usd) if fx else None,
        "first": first,
        "realized": rnd(realized),
        "fees": rnd(fees),
        "dividends": rnd(divs),
        "events": applied,
        "years": [{k: (rnd(v) if k != "year" else v) for k, v in y.items()} for y in sorted(years.values(), key=lambda y: y["year"])],
        "warnings": warnings,
    }


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


def corporate(entry: Entry, actions: list[dict] | None, dividend_tax_pct: float = 0.0) -> dict:
    """Bonus shares / splits and dividends since my buy date, applied to what I hold (long only).

    Yahoo's prices are split-adjusted and its dividends are per current share, so after
    multiplying my quantity by the split factor, dividend x quantity is what was actually paid.
    """
    none = {"factor": 1.0, "events": [], "dividends": 0.0, "dividendsGross": 0.0, "needsDate": False}
    if entry.position != "long" or not actions:
        return none
    if entry.since is None:
        return none | {"needsDate": True}
    since = entry.since.isoformat()
    after = [a for a in actions if a["date"] > since]
    factor = math.prod(a["split"] for a in after if a["split"] > 0)
    qty = (entry.qty or 0) * factor
    gross = sum(a["dividend"] for a in after) * qty
    events = [{"date": a["date"], "kind": "bonus" if a["split"] > 1 else "split", "ratio": a["split"]}
              for a in after if a["split"] > 0]
    events += [{"date": a["date"], "kind": "dividend", "perShare": a["dividend"]} for a in after if a["dividend"] > 0]
    return {
        "factor": round(factor, 6),
        "events": sorted(events, key=lambda e: e["date"]),
        "dividendsGross": round(gross, 2),
        "dividends": round(gross * (1 - dividend_tax_pct / 100), 2),
        "needsDate": False,
    }


def _net_per_share(side: str, avg: float, last: float, fee: float, gains_tax: float) -> float:
    """Profit per share if closed now, after buying and selling costs and (on a gain) the gains tax."""
    gain = last * (1 - fee) - avg * (1 + fee) if side == "long" else avg * (1 - fee) - last * (1 + fee)
    return gain * (1 - gains_tax) if gain > 0 else gain


def usd_rates() -> pd.Series | None:
    """EGP per USD, daily (Yahoo, else TradingView); None when neither answers."""
    for code in ("USDEGP=X", "FX_IDC:USDEGP"):
        try:
            _, df, _ = fetch_ohlcv(code, "1d", "10y")
        except Exception:
            continue
        c = df["Close"]
        c.index = c.index.tz_localize(None) if c.index.tz is not None else c.index
        return c
    return None


def fx_lookup(rates: pd.Series):
    """fx(date) -> EGP per USD on or just before that date; fx(None) -> latest."""
    def fx(d=None) -> float:
        if d is None:
            return float(rates.iloc[-1])
        before = rates[: pd.Timestamp(d)]
        return float(before.iloc[-1] if len(before) else rates.iloc[0])
    return fx


def usd_view(eff: Entry, journal: dict | None, last: float, currency: str | None, settings: Settings, fx) -> dict | None:
    """The same holding measured in dollars: what I paid at the rate back then vs what it's worth now."""
    if fx is None or currency != "EGP" or eff.position != "long" or not eff.qty or not eff.since or not eff.avgCost:
        return None
    f = settings.feePct / 100
    now, then = fx(None), fx(eff.since)
    cost = journal["costUsd"] if journal and journal.get("costUsd") else eff.qty * eff.avgCost * (1 + f) / then
    value = eff.qty * last * (1 - f) / now
    return {
        "rateThen": round(then, 3), "rateNow": round(now, 3),
        "costUsd": round(cost, 2), "valueUsd": round(value, 2), "plUsd": round(value - cost, 2),
        "plUsdPct": round((value / cost - 1) * 100, 2) if cost else None,
        "egpChangePct": round((then / now - 1) * 100, 2),  # negative: the pound lost value against the dollar
    }


def compare(entry: Entry, live: dict, track: dict | None = None, settings: Settings | None = None,
            actions: list[dict] | None = None, fx=None, ctx=None, interval: str = "1d") -> dict:
    """Merge my entry with the live scan row: P/L, distance to my levels, and whether the chart agrees.

    With `ctx` (a decision.Context: fundamentals, sectors and my whole portfolio) buys also have to pass the
    buy checklist and fit my cash, total risk and sector limits, and results dates are taken into account.
    """
    settings = settings or Settings()
    out = entry.model_dump(mode="json")
    if "error" in live:
        return out | {"error": live["error"]}
    last = live["last"]
    journal = None
    if entry.trades:
        # My trades decide what I hold; a fully sold position becomes a watch entry.
        journal = ledger(entry.trades, actions, settings, fx)
        held = journal["qty"] > 0
        eff = entry.model_copy(update={"position": "long" if held else "none", "qty": journal["qty"] if held else None,
                                       "avgCost": journal["avgCost"], "since": date.fromisoformat(journal["first"])})
        corp = {"factor": 1.0, "events": journal["events"], "dividends": journal["dividends"],
                "dividendsGross": None, "needsDate": False}
    else:
        corp = corporate(entry, actions, settings.dividendTaxPct)
        # Work on my position as it is today: bonus shares raise the quantity and lower the cost per share.
        eff = entry
        if corp["factor"] != 1 and entry.qty:
            eff = entry.model_copy(update={"qty": entry.qty * corp["factor"],
                                           "avgCost": entry.avgCost / corp["factor"] if entry.avgCost else None})
    sign = {"long": 1, "short": -1}.get(eff.position, 0)
    view = _app_view(live["score"])
    fee, gtax = settings.feePct / 100, settings.gainsTaxPct / 100

    status = "open"
    # Which way my target/stop point: a held position's stop protects the position; when only
    # watching, they follow my expectation.
    up = eff.position == "long" or (eff.position == "none" and entry.expectation != "bearish")
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

    pl_pct = pl_value = break_even = None
    if sign and eff.avgCost:
        side = eff.position
        per = _net_per_share(side, eff.avgCost, last, fee, gtax)
        pl_pct = round(per / (eff.avgCost * (1 + fee)) * 100, 2)
        pl_value = round(per * eff.qty, 2) if eff.qty else None
        break_even = round(eff.avgCost * (1 + fee) / (1 - fee) if side == "long" else eff.avgCost * (1 - fee) / (1 + fee), 4)

    adtv = live.get("adtv")
    fund = ctx.fund(live["symbol"]) if ctx else None
    sector = (fund or {}).get("sector")
    book = ctx.book if ctx else None
    res = out | {
        "position": eff.position,
        "since": eff.since.isoformat() if eff.since else None,
        "journal": journal,
        "resolved": live["symbol"],
        "name": live["name"],
        "currency": live["currency"],
        "last": last,
        "changePct": live["changePct"],
        "adjQty": round(eff.qty, 4) if eff.qty is not None else None,
        "adjAvgCost": round(eff.avgCost, 4) if eff.avgCost is not None else None,
        "corporate": corp,
        "plPct": pl_pct,
        "plValue": pl_value,
        "breakEven": break_even,
        "income": corp["dividends"],
        "totalReturn": round(pl_value + corp["dividends"], 2) if pl_value is not None else None,
        "adtv": adtv,
        "thin": bool(adtv is not None and live["currency"] and adtv < settings.minAdtv),
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
        "usd": usd_view(eff, journal, last, live["currency"], settings, fx),
        "sector": sector,
        "lastBar": live.get("lastBar"),
        **_money(eff, live, settings, book, sector),
    }
    if ctx:
        from . import decision
        res["decision"] = ctx.evaluate(live, interval, entry=eff, sizing=res["sizing"] if eff.position == "none" else None)
        res["nextEarnings"] = res["decision"].get("nextEarnings")
        res["earningsInDays"] = (date.fromisoformat(res["nextEarnings"]) - ctx.today).days if res["nextEarnings"] else None
        res["stale"] = bool(live.get("currency")) and decision.check_fresh(live, ctx.today, interval, settings.holidays)["status"] == "warn"
    action = action_for(eff, res, settings, book, sector)
    return res | {"action": action, "ticket": order_ticket(action, res, settings)}


SELL_ALL = {"Cut loss", "Exit (stop)", "Take profit", "Sell"}


def order_ticket(action: dict, e: dict, settings: Settings) -> dict | None:
    """A limit order ready to type into the broker app, for actions that come with a quantity."""
    tick = settings.tickSize
    last, f = e["last"], settings.feePct / 100
    code = e["symbol"].removesuffix(".CA")
    if action["group"] == "Buy" and action.get("shares"):
        qty = int(action["shares"])
        limit = math.ceil(last / tick - 1e-9) * tick
        stop = (e.get("sizing") or {}).get("stop") if action["action"] == "Buy" else e.get("stop")
        total = qty * limit * (1 + f)
        text = f"BUY {qty:,} {code} @ {limit:,.2f} limit · about {total:,.2f} incl. costs"
    elif action["group"] == "Sell" and e["position"] == "long" and (action.get("shares") or action["action"] in SELL_ALL):
        qty = int(action.get("shares") or math.floor(e.get("adjQty") or e.get("qty") or 0))
        if qty < 1:
            return None
        limit = math.floor(last / tick + 1e-9) * tick
        stop = e.get("stop") if action["action"] == "Trim" else None
        total = qty * limit * (1 - f)
        text = f"SELL {qty:,} {code} @ {limit:,.2f} limit · about {total:,.2f} after costs"
    else:
        return None
    if stop:
        text += f" · then watch a stop at {stop:,.2f}"
    return {"side": "BUY" if action["group"] == "Buy" else "SELL", "symbol": code, "qty": qty,
            "limit": round(limit, 4), "total": round(total, 2), "stop": stop, "text": text}


def _money(entry: Entry, live: dict, settings: Settings, book: dict | None = None, sector: str | None = None) -> dict:
    """Sizing for a new/added buy, and what an open position means for the account."""
    last = live["last"]
    mine = entry.stop is not None and entry.stop < last
    stop = entry.stop if mine else live.get("chartStop")
    out = {"sizing": None, "exposure": None}
    if entry.position != "short":
        # a held position is already in the book, so only a new buy is cut to fit the rest of the portfolio
        new = entry.position == "none"
        out["sizing"] = sizing_for(settings, live["currency"], last, stop, live.get("adtv"), book if new else None,
                                   sector) | {"stopSource": "yours" if mine else "chart"}
    same_money = settings.capital and live["currency"] and live["currency"].upper() == settings.currency
    if entry.position != "none" and entry.qty and same_money:
        out["exposure"] = position_risk(settings.capital, entry.position, entry.qty, last, entry.stop, settings.feePct)
    return out


def _track_line(track: dict | None, bias: str) -> dict | None:
    """How the chart's current bias has played out on this symbol before."""
    if not track:
        return None
    pick = lambda t: next((b for b in t["buckets"] if b["bias"] == bias and b["n"]), None) if t else None
    b = pick(track)
    if not b:
        return None
    r = pick(track.get("recent"))
    recent = r and {k: r[k] for k in ("n", "asExpected", "avgRet", "excess", "small")} | {
        "baseline": track["recent"]["baseline"], "range": track["recent"]["range"]}
    return {k: b[k] for k in ("bias", "n", "asExpected", "avgRet", "excess", "small")} | {"baseline": track["baseline"], "recent": recent}


GROUP = {"buy": "Buy", "sell": "Sell", "hold": "Hold", "caution": "Hold", "wait": "Wait"}


def _act(action: str, tone: str, reason: str, **extra) -> dict:
    """`action` is the specific tag (e.g. "Cut loss"); `group` is the plain Buy / Hold / Sell / Wait."""
    return {"action": action, "tone": tone, "group": GROUP[tone], "reason": reason} | extra


NEAR_STOP_PCT = 3.0


def _room_to_add(entry: Entry, last: float, settings: Settings, adtv: float | None = None,
                 book: dict | None = None, sector: str | None = None) -> int:
    """Extra shares my rules still allow on top of what I hold (0 if none or unknown).
    With `book`, also no more than my cash, total risk and sector limits leave room for."""
    if not (settings.capital and entry.qty and entry.stop and entry.stop < last):
        return 0
    max_shares = int(adtv * settings.maxAdtvPct / 100 / last) if adtv else None
    z = size_position(settings.capital, settings.riskPct, settings.maxPositionPct, last, entry.stop, settings.feePct, max_shares)
    extra = max(0, z["shares"] - int(entry.qty)) if z["ok"] else 0
    if extra and book:
        extra = min([extra] + [n for n, _ in portfolio_caps(settings, book, sector, last, entry.stop).values()])
    return max(0, extra)


def _buy_block(e: dict, settings: Settings) -> str | None:
    """Why a buy should wait, from the buy checklist and results dates (None: nothing against it)."""
    d = e.get("decision")
    if d and d.get("applicable") and d["verdict"] in ("avoid", "wait"):
        return f"The buy checklist says {d['label']}: {d['reason']}"
    days = e.get("earningsInDays")
    if days is not None and 0 <= days <= settings.earningsBlackoutDays:
        return f"Results are due on {e['nextEarnings']} (in {days} days); a surprise can gap the price past your stop."
    return None


def action_for(entry: Entry, e: dict, settings: Settings, book: dict | None = None, sector: str | None = None) -> dict:
    """Buy / Hold / Sell from my own plan (view, stop, target) and my money rules.

    The chart's bias is deliberately not an input: the track record shows it has no reliable edge.
    A buy or add also has to pass the buy checklist (when there is one) and keep clear of results dates.
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
        extra = _room_to_add(entry, last, settings, e.get("adtv"), book, sector)
        between = f"between your stop ({f(stop)})" + (f" and target ({f(target)})" if target else "")
        if entry.expectation == "bullish" and target and extra and (target - last) >= (last - stop):
            block = _buy_block(e, settings)
            if block:
                return _act("Hold", "hold", f"The price is {between}. Your plan says stay in, but don't add yet. {block}")
            return _act("Add more", "buy", f"Your view is still bullish, your target ({f(target)}) offers more than your stop risks, "
                                           f"and your rules allow up to {extra:,} more shares.", shares=extra)
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
    block = _buy_block(e, settings)
    if block:
        return _act("Wait", "wait", f"Your view is bullish, but wait. {block}")
    d = e.get("decision")
    gap = f" The buy checklist has too little data to judge it ({d['reason'].rstrip('.').lower()})." if d and d.get("verdict") == "unknown" else ""
    if z.get("ok"):
        whose = "your stop" if z.get("stopSource") == "yours" else "a stop"
        cut = f" Capped because {z['limitNote']}" if z.get("limitNote") else ""
        return _act("Buy", "buy", f"Your view is bullish. Buy up to {z['shares']:,} shares with {whose} at {f(z['stop'])}; "
                                  f"that risks {z['riskPct']:g}% of your capital.{cut}{gap}", shares=z["shares"])
    if z.get("configured") is False:
        return _act("Buy", "buy", "Your view is bullish. Set your capital in My money & rules to see how many shares.")
    if z.get("reason", "").startswith("An index"):
        return _act("Buy via fund", "buy", "Your view is bullish. An index is bought through a fund that tracks it.")
    if any(x in z.get("reason", "") for x in ("Even one share", "One share costs", "No room")):
        return _act("Wait", "wait", z["reason"])
    return _act("Buy", "buy", f"Your view is bullish. {z.get('reason', '')}".strip())




def summarize(entries: list[dict], settings: Settings | None = None, book: dict | None = None) -> dict:
    """Totals per currency, how often the chart agrees with me, and what needs a look.
    `book` (book_of) gives real cash and sector weights; without it cash is capital minus what's invested."""
    held = [e for e in entries if e["position"] != "none"]
    books: dict[str, dict] = {}
    fee = (settings.feePct if settings else Settings().feePct) / 100
    for e in held:
        if e.get("plValue") is None:
            continue
        cost = e["adjAvgCost"] * e["adjQty"] * (1 + fee)
        b = books.setdefault(e.get("currency") or "", {"currency": e.get("currency") or "", "positions": 0,
                                                       "cost": 0.0, "pl": 0.0, "income": 0.0})
        b["positions"] += 1
        b["cost"] += cost
        b["pl"] += e["plValue"]
        b["income"] += e.get("income") or 0.0
        if e.get("usd"):
            b["costUsd"] = b.get("costUsd", 0.0) + e["usd"]["costUsd"]
            b["valueUsd"] = b.get("valueUsd", 0.0) + e["usd"]["valueUsd"]
    by_currency = [
        b | {"cost": round(b["cost"], 2), "pl": round(b["pl"], 2), "value": round(b["cost"] + b["pl"], 2),
             "income": round(b["income"], 2),
             "plPct": round(b["pl"] / b["cost"] * 100, 2) if b["cost"] else None,
             "totalPct": round((b["pl"] + b["income"]) / b["cost"] * 100, 2) if b["cost"] else None,
             "usdPct": round((b["valueUsd"] / b["costUsd"] - 1) * 100, 2) if b.get("costUsd") else None}
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
        if e.get("thin") and e["position"] != "none":
            attention.append({"symbol": s, "kind": "thin",
                              "text": f"Thinly traded (about {e['adtv']:,.0f} a day), so selling quickly could move the price."})
        if (e.get("corporate") or {}).get("needsDate"):
            attention.append({"symbol": s, "kind": "date",
                              "text": "Add your buy date so bonus shares and dividends are counted in your P/L."})
        if e["position"] != "none" and e.get("agreement") == "disagrees":
            attention.append({"symbol": s, "kind": "disagree",
                              "text": f"You hold it expecting {e['expectation']}, but the chart reads {e['appBias'].lower()}."})
        days = e.get("earningsInDays")
        if e["position"] != "none" and days is not None and 0 <= days <= (settings or Settings()).earningsBlackoutDays:
            attention.append({"symbol": s, "kind": "earnings",
                              "text": f"Results due on {e['nextEarnings']} (in {days} days). The price can jump either way; "
                                      "check your stop, or trim if a gap past it would hurt too much."})
        if e.get("stale"):
            attention.append({"symbol": s, "kind": "stale",
                              "text": f"No new price since {e['lastBar']}: suspended, or the data feed is behind. Check before acting."})

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
            "maxTotalRiskPct": settings.maxTotalRiskPct,
            "maxSectorPct": settings.maxSectorPct,
            "invested": round(invested, 2),
            "investedPct": round(invested / settings.capital * 100, 1),
            "cash": book["cash"] if book else round(settings.capital - invested, 2),
            "cashReal": bool(book),  # capital + realized + dividends - cost of what I hold
            "atRisk": round(sum(x["risk"] or 0 for _, x in exp), 2),
            "atRiskPct": round(sum(x["risk"] or 0 for _, x in exp) / settings.capital * 100, 2),
            "noStop": [e["symbol"] for e, x in exp if x["risk"] is None],
            "sectors": sorted(({"sector": k, "value": v, "pct": round(v / settings.capital * 100, 1)}
                               for k, v in (book or {}).get("bySector", {}).items()), key=lambda r: -r["value"]),
        }
        if risk["atRiskPct"] > settings.maxTotalRiskPct:
            attention.append({"symbol": "Portfolio", "kind": "total-risk",
                              "text": f"If every stop is hit you lose {risk['atRiskPct']:g}% of your capital, above your "
                                      f"{settings.maxTotalRiskPct:g}% limit. Tighten stops or trim before buying more."})
        for r in risk["sectors"]:
            if r["pct"] > settings.maxSectorPct:
                attention.append({"symbol": r["sector"], "kind": "sector",
                                  "text": f"is {r['pct']:g}% of your capital, above your {settings.maxSectorPct:g}% limit per sector."})
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

    ledgers: dict[tuple[str, str], dict] = {}
    for e in entries:
        j = e.get("journal")
        for y in (j or {}).get("years", []):
            g = ledgers.setdefault((e.get("currency") or "", y["year"]),
                                   {"currency": e.get("currency") or "", "year": y["year"], "realized": 0.0, "dividends": 0.0, "fees": 0.0})
            for k in ("realized", "dividends", "fees"):
                g[k] += y[k]
    years = sorted(({k: (round(v, 2) if isinstance(v, float) else v) for k, v in g.items()} for g in ledgers.values()),
                   key=lambda g: (g["year"], g["currency"]), reverse=True)

    count = lambda k, v: sum(1 for e in entries if e.get(k) == v)
    todo: dict[str, list[str]] = {"buy": [], "hold": [], "sell": [], "wait": []}
    for e in entries:
        a = e.get("action")
        if a:
            todo[a["group"].lower()].append(e["symbol"])
    return {
        "years": years,
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


def _actions_for(entry: Entry, row: dict) -> list[dict]:
    # Only held stocks need them (indices have none, and Yahoo logs noise when asked).
    if (entry.position != "long" and not entry.trades) or "error" in row or not row.get("currency"):
        return []
    return fetch_actions(row["symbol"])


def live_profile(interval: str, period: str) -> dict:
    prof = load()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda e: _scan_one(e.symbol, interval, period), prof.entries))
        acts = list(pool.map(_actions_for, prof.entries, rows))
    need_fx = any(r.get("currency") == "EGP" and (e.position == "long" or e.trades) for e, r in zip(prof.entries, rows))
    rates = usd_rates() if need_fx else None
    fx = fx_lookup(rates) if rates is not None else None
    tracks = [backtest.track_for(r["symbol"], interval) if "error" not in r else None for r in rows]
    # Two passes: the first gives every position's cost, value and risk (the book), the second
    # sizes buys against what that book leaves and runs the buy checklist.
    ctx = decision.Context(prof, book={})
    first = [compare(e, r, t, prof.settings, a, fx) for e, r, t, a in zip(prof.entries, rows, tracks, acts)]
    ctx.book = book_of(first, prof.settings, lambda s: (ctx.fund(s) or {}).get("sector"))
    entries = [compare(e, r, t, prof.settings, a, fx, ctx, interval) for e, r, t, a in zip(prof.entries, rows, tracks, acts)]
    for e, r in zip(entries, rows):
        e["halal"] = shariah.status(prof.shariah, r.get("symbol") or e["symbol"])
    return {"name": prof.name, "settings": public_settings(prof.settings), "entries": entries,
            "summary": summarize(entries, prof.settings, ctx.book)}
