"""Halal (Shariah-compliant) tags: the official EGX 33 Shariah list I paste in, plus my own tags.

TradingView doesn't publish the members of the EGX 33 Shariah index, so the list comes from EGX's
constituents file (reviewed twice a year). Being outside the list doesn't make a stock non-compliant:
the index holds only the most liquid compliant stocks. So such a stock stays untagged unless I tag it.
"""
from __future__ import annotations

import re
from datetime import date

STALE_DAYS = 200  # EGX reviews the index every six months; older than this, the list may be out of date
ISIN = re.compile(r"^EG[A-Z0-9]{10}$")


def key(symbol: str) -> str:
    """One spelling per stock: EGX:COMI and COMI.CA are both COMI.CA."""
    s = symbol.strip().upper()
    return s[4:] + ".CA" if s.startswith("EGX:") else s


def status(sh, symbol: str) -> dict | None:
    """{"status": "halal" | "not", "source": "mine" | "list"} for a resolved symbol; None when unknown.
    `sh` is the profile's Shariah settings. My own tag wins over the list."""
    k = key(symbol)
    mine = sh.tags.get(k)
    if mine:
        return {"status": mine, "source": "mine"}
    if k in sh.symbols:
        return {"status": "halal", "source": "list", "listDate": sh.listDate.isoformat() if sh.listDate else None}
    return None


def halal_symbols(sh) -> list[str]:
    """Everything I count as halal: the list, plus my halal tags, minus my not-halal tags."""
    out = list(dict.fromkeys(sh.symbols + [s for s, v in sh.tags.items() if v == "halal"]))
    return [s for s in out if sh.tags.get(s) != "not"]


def is_stale(list_date: date | None, today: date) -> bool:
    return list_date is not None and (today - list_date).days > STALE_DAYS


def parse_list(text: str, market: list[dict] | None) -> tuple[list[str], list[str]]:
    """(symbols, unknown) from pasted text: a plain list of codes, or a table copied from EGX.

    Matches ISINs (EGS60121C018), Reuters codes (COMI.CA), TradingView codes (EGX:COMI) and bare tickers.
    When the paste has ISINs it's a table from EGX, so bare words are ignored: they're company names.
    `market` is the live EGX stock list ({"symbol", "isin"}) to check against; None accepts any code.
    """
    tokens = re.findall(r"[A-Z0-9][A-Z0-9.:]*[A-Z0-9]", text.upper())
    by_isin = {s["isin"]: s["symbol"] for s in market or [] if s.get("isin")}
    known = {s["symbol"] for s in market} if market is not None else None
    structured = any(ISIN.match(t) for t in tokens)
    found, unknown = [], []
    for t in tokens:
        if ISIN.match(t):
            sym = by_isin.get(t)
            if sym is None and market is not None:
                unknown.append(t)
                continue
            if sym is None:
                continue  # can't map an ISIN without the market list
        elif t.endswith(".CA") or t.startswith("EGX:"):
            sym = key(t)
        elif structured or not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", t):
            continue
        else:
            sym = t + ".CA"
        if known is not None and sym not in known:
            unknown.append(t)
            continue
        found.append(sym)
    return list(dict.fromkeys(found)), list(dict.fromkeys(unknown))
