"""OHLCV fetching with symbol normalization and caching.

Yahoo Finance is the primary source. TradingView fills the gaps Yahoo has for
the Egyptian Exchange: EGX indices only have a live quote on Yahoo.
"""
from __future__ import annotations

import re
import threading

import pandas as pd
import yfinance as yf
from cachetools import TTLCache

from .tradingview import fetch_tv

VALID_INTERVALS = {"1h", "1d", "1wk", "1mo"}
VALID_PERIODS = {"3mo", "6mo", "1y", "2y", "5y", "10y", "max"}
# Yahoo only serves ~730 days of hourly data.
INTRADAY_PERIODS = {"3mo", "6mo", "1y", "2y"}

# Friendly aliases for indices users commonly type without Yahoo's prefix.
ALIASES = {
    "SPX": "^GSPC",
    "SP500": "^GSPC",
    "NDX": "^NDX",
    "NASDAQ": "^IXIC",
    "DJI": "^DJI",
    "DOW": "^DJI",
    "DAX": "^GDAXI",
    "FTSE": "^FTSE",
    "NIKKEI": "^N225",
    "VIX": "^VIX",
}

# EGX indices Yahoo has no history for, keyed by the input with spaces/punctuation removed.
TV_ALIASES = {
    "EGX30": "EGX:EGX30",
    "CASE30": "EGX:EGX30",
    "EGX33": "EGX:SHARIAH",
    "EGX33SHARIAH": "EGX:SHARIAH",
    "SHARIAH": "EGX:SHARIAH",
    "SHARIAHCA": "EGX:SHARIAH",
    "EGX70": "EGX:EGX70EWI",
    "EGX70EWI": "EGX:EGX70EWI",
    "EGX70EWICA": "EGX:EGX70EWI",
    "EGX100": "EGX:EGX100EWI",
    "EGX100EWI": "EGX:EGX100EWI",
    "EGX100EWICA": "EGX:EGX100EWI",
    "EGX30CAPPED": "EGX:EGX30CAPPED",
    "EGX30CAPPEDCA": "EGX:EGX30CAPPED",
}

_cache: TTLCache = TTLCache(maxsize=256, ttl=300)
_lock = threading.Lock()


class SymbolNotFound(Exception):
    pass


class InsufficientHistory(Exception):
    """Symbol exists but the provider returns too few bars to analyze."""

    def __init__(self, symbol: str, bars: int):
        super().__init__(symbol)
        self.symbol, self.bars = symbol, bars


def candidates(symbol: str) -> list[tuple[str, str]]:
    """Ordered (provider, code) pairs to try for a user-typed symbol."""
    s = symbol.strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", s)
    if ":" in s:
        return [("tv", s.replace(" ", ""))]
    if compact in TV_ALIASES:
        return [("tv", TV_ALIASES[compact])]
    if s in ALIASES:
        return [("yahoo", ALIASES[s])]
    out = [("yahoo", s)]
    if s.endswith(".CA"):
        out.append(("tv", "EGX:" + s[:-3].lstrip("^")))
    elif not any(c in s for c in ".^="):
        out += [("yahoo", f"{s}.CA"), ("yahoo", f"^{s}"), ("tv", f"EGX:{s}")]
    return out


def _download(symbol: str, interval: str, period: str) -> tuple[pd.DataFrame, dict]:
    if interval == "1h" and period not in INTRADAY_PERIODS:
        period = "2y"
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval, auto_adjust=False, actions=False)
    if df is None or df.empty:
        return pd.DataFrame(), {}
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["High"] > 0) & (df["Low"] > 0)]
    meta = getattr(t, "history_metadata", None) or {}
    return df, meta


def fetch_ohlcv(symbol: str, interval: str = "1d", period: str = "1y") -> tuple[str, pd.DataFrame, dict]:
    """Return (resolved_symbol, dataframe, metadata).

    Raises SymbolNotFound, or InsufficientHistory when the symbol resolves but
    has fewer than 30 bars.
    """
    key = (symbol.strip().upper(), interval, period)
    with _lock:
        if key in _cache:
            return _cache[key]
    short: tuple[str, int] | None = None
    for provider, sym in candidates(symbol):
        try:
            df, meta = _download(sym, interval, period) if provider == "yahoo" else fetch_tv(sym, interval, period)
        except Exception:  # provider errors just move on to the next candidate
            continue
        if len(df) >= 30:
            result = (sym, df, meta)
            with _lock:
                _cache[key] = result
            return result
        if len(df) and short is None:
            short = (sym, len(df))
    if short:
        raise InsufficientHistory(*short)
    raise SymbolNotFound(symbol)
