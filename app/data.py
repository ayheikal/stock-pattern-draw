"""OHLCV fetching with symbol normalization and caching.

TradingView is the primary source for the Egyptian Exchange: Yahoo publishes EGX
sessions a day or more late and has no history for EGX indices. Yahoo Finance serves
everything else, and is the fallback for EGX stocks TradingView can't find.
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
    "USDEGP": "EGP=X",
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
# 5m / 15m bars (the Intraday page) go stale fast: keep them a minute, not five.
FAST_INTERVALS = {"5m", "15m"}
_fast_cache: TTLCache = TTLCache(maxsize=256, ttl=60)
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
    if s.endswith(".CA"):
        return [("tv", "EGX:" + s[:-3].lstrip("^")), ("yahoo", s)]
    out = [("yahoo", s)]
    if not any(c in s for c in ".^="):
        out += [("tv", f"EGX:{s}"), ("yahoo", f"{s}.CA"), ("yahoo", f"^{s}")]
    return out


def _download(symbol: str, interval: str, period: str) -> tuple[pd.DataFrame, dict]:
    if interval == "1h" and period not in INTRADAY_PERIODS:
        period = "2y"
    if interval in FAST_INTERVALS and period not in ("5d", "1mo"):
        period = "60d"  # all Yahoo keeps of 5m / 15m bars
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval, auto_adjust=False, actions=False)
    if df is None or df.empty:
        return pd.DataFrame(), {}
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["High"] > 0) & (df["Low"] > 0)]
    meta = getattr(t, "history_metadata", None) or {}
    return df, meta


_actions_cache: TTLCache = TTLCache(maxsize=256, ttl=12 * 3600)


def fetch_actions(resolved: str) -> list[dict]:
    """Dividends and splits/bonus shares for a resolved symbol, oldest first.

    Yahoo only; a TradingView code like EGX:COMI is looked up as COMI.CA. Indices return [].
    Yahoo's dividends are per share *after* later splits, matching its split-adjusted prices.
    """
    code = resolved.strip().upper()
    if code.startswith("EGX:"):
        code = code[4:] + ".CA"
    elif ":" in code:
        return []
    with _lock:
        if code in _actions_cache:
            return _actions_cache[code]
    out: list[dict] = []
    try:
        a = yf.Ticker(code).actions
    except Exception:  # no actions is the safe answer; P/L just isn't adjusted
        a = None
    if a is not None and not a.empty:
        for ts, row in a.iterrows():
            div, split = float(row.get("Dividends", 0) or 0), float(row.get("Stock Splits", 0) or 0)
            if div > 0 or split > 0:
                out.append({"date": ts.strftime("%Y-%m-%d"), "dividend": div, "split": split})
    with _lock:
        _actions_cache[code] = out
    return out


def is_egx(resolved: str) -> bool:
    """A resolved symbol priced in Egyptian pounds on the EGX (EGX:COMI, EGX:EGX30, COMI.CA)."""
    s = resolved.upper()
    return s.startswith("EGX:") or s.endswith(".CA")


def in_usd(closes: pd.Series, rate: pd.Series, interval: str) -> pd.DataFrame:
    """EGP closes divided by the USD/EGP rate at each bar: the latest hourly rate intraday, the day's rate on
    daily bars, and the rate on the bar's last day on weekly / monthly ones (they're dated at their start).
    Columns usd and rate, NaN before the rate's history begins."""
    if interval == "1h":
        key = lambda ix: ix.tz_convert("UTC") if ix.tz is not None else ix.tz_localize("UTC")
    else:
        key = lambda ix: (ix.tz_localize(None) if ix.tz is not None else ix).normalize()
    rate = pd.Series(rate.to_numpy(), index=key(rate.index)).sort_index()
    rate = rate[~rate.index.duplicated(keep="last")]
    at = key(closes.index)
    if interval in ("1wk", "1mo"):
        at = (at[1:] - pd.Timedelta(days=1)).append(pd.DatetimeIndex([pd.Timestamp.max.normalize()]))
    picked = rate.reindex(at, method="ffill").to_numpy()
    return pd.DataFrame({"usd": closes.to_numpy() / picked, "rate": picked}, index=closes.index)


def fetch_ohlcv(symbol: str, interval: str = "1d", period: str = "1y") -> tuple[str, pd.DataFrame, dict]:
    """Return (resolved_symbol, dataframe, metadata).

    Raises SymbolNotFound, or InsufficientHistory when the symbol resolves but
    has fewer than 30 bars.
    """
    key = (symbol.strip().upper(), interval, period)
    cache = _fast_cache if interval in FAST_INTERVALS else _cache
    with _lock:
        if key in cache:
            return cache[key]
    short: tuple[str, int] | None = None
    for provider, sym in candidates(symbol):
        try:
            df, meta = _download(sym, interval, period) if provider == "yahoo" else fetch_tv(sym, interval, period)
        except Exception:  # provider errors just move on to the next candidate
            continue
        if len(df) and (df["High"] == df["Low"]).mean() > 0.9:
            continue  # frozen quotes (suspended/delisted): nothing to analyze
        if len(df) >= 30:
            result = (sym, df, meta)
            with _lock:
                cache[key] = result
            return result
        if len(df) and short is None:
            short = (sym, len(df))
    if short:
        raise InsufficientHistory(*short)
    raise SymbolNotFound(symbol)
