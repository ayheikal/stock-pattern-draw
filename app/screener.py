"""Scan a list of symbols and rank those whose outlook is bullish."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .data import InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .outlook import build_outlook
from .patterns import analyze

# Built-in watchlists. Unknown or delisted codes are simply reported as skipped.
UNIVERSES: dict[str, dict] = {
    "egx": {
        "label": "Egypt (EGX indices & large caps)",
        "symbols": [
            "EGX30", "EGX33", "EGX70", "EGX100",
            "COMI.CA", "TMGH.CA", "EAST.CA", "ETEL.CA", "HRHO.CA", "SWDY.CA", "ABUK.CA", "EFIH.CA",
            "FWRY.CA", "ORAS.CA", "AMOC.CA", "JUFO.CA", "HELI.CA", "PHDC.CA",
            "MFPC.CA", "CIEB.CA", "ADIB.CA", "EMFD.CA", "ORWE.CA", "SKPC.CA", "CCAP.CA", "BTFH.CA",
            "ALCN.CA", "GBCO.CA", "MASR.CA", "RAYA.CA", "ISPH.CA", "EGAL.CA", "EFIC.CA", "OCDI.CA",
        ],
    },
    "world": {
        "label": "World indices",
        "symbols": [
            "^GSPC", "^NDX", "^DJI", "^RUT", "^GSPTSE", "^BVSP", "^FTSE", "^GDAXI", "^FCHI", "^STOXX50E",
            "^N225", "^HSI", "000001.SS", "^KS11", "^BSESN", "^AXJO", "EGX30",
        ],
    },
    "us": {
        "label": "US large caps",
        "symbols": [
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "JPM", "LLY", "V",
            "UNH", "XOM", "MA", "COST", "HD", "PG", "JNJ", "NFLX", "AMD", "ORCL", "CRM", "ASML",
        ],
    },
}
MAX_SYMBOLS = 80


def _scan_one(symbol: str, interval: str, period: str) -> dict:
    try:
        return _analyze_one(symbol, interval, period)
    except (SymbolNotFound, InsufficientHistory):
        return {"symbol": symbol, "error": "no data"}
    except Exception as e:  # one bad symbol shouldn't sink the whole scan
        return {"symbol": symbol, "error": str(e)[:80]}


def _analyze_one(symbol: str, interval: str, period: str) -> dict:
    resolved, df, meta = fetch_ohlcv(symbol, interval, period)
    patterns, levels = analyze(df)
    o = build_outlook(df, patterns, levels, interval)
    close = df["Close"]
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    buy = next((p for p in o["plans"] if p["side"] == "long"), None)
    return {
        "symbol": resolved,
        "query": symbol,
        "name": meta.get("longName") or meta.get("shortName") or resolved,
        "currency": meta.get("currency"),
        "last": round(last, 4),
        "changePct": round((last - prev) / prev * 100, 2),
        "bias": o["bias"],
        "score": o["score"],
        "upTarget": o["upTarget"],
        "upsidePct": round((o["upTarget"] - last) / last * 100, 2),
        "stop": buy["stop"] if buy else None,
        "rr": buy["rr"] if buy else None,
        "action": o["decision"]["newcomer"]["action"],
        "aboveSma200": None if sma200 is None else last >= sma200,
        "signal": o["drivers"][0]["text"] if o["drivers"] else "",
    }


def scan(symbols: list[str], interval: str, period: str, min_score: float) -> dict:
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda s: _scan_one(s, interval, period), symbols))
    ok = [r for r in rows if "error" not in r]
    bullish = sorted((r for r in ok if r["score"] >= min_score), key=lambda r: (-r["score"], -r["upsidePct"]))
    return {
        "scanned": len(ok),
        "skipped": [r["symbol"] for r in rows if "error" in r],
        "results": bullish,
    }
