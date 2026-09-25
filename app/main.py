from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data import VALID_INTERVALS, VALID_PERIODS, InsufficientHistory, SymbolNotFound, fetch_ohlcv
from .patterns import analyze

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Pattern Charts", version="1.0")


def _times(index: pd.DatetimeIndex, interval: str) -> list:
    """Chart times: 'YYYY-MM-DD' for daily+ bars, exchange wall-clock epoch seconds intraday."""
    if interval in ("1d", "1wk", "1mo"):
        return [ts.strftime("%Y-%m-%d") for ts in index]
    naive = index.tz_localize(None) if index.tz is not None else index
    return [int(ts.timestamp()) for ts in naive.tz_localize("UTC")]


def _r(x: float) -> float:
    return round(float(x), 4)


@app.get("/api/analyze")
def api_analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    interval: str = Query("1d"),
    period: str = Query("1y"),
):
    if interval not in VALID_INTERVALS:
        raise HTTPException(400, f"interval must be one of {sorted(VALID_INTERVALS)}")
    if period not in VALID_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(VALID_PERIODS)}")
    try:
        resolved, df, meta = fetch_ohlcv(symbol, interval, period)
    except InsufficientHistory as e:
        raise HTTPException(
            422,
            f"'{e.symbol}' was found, but only {e.bars} bar(s) of history are available, "
            "which is not enough to analyze. Try a longer period or a daily interval.",
        )
    except SymbolNotFound:
        raise HTTPException(
            404,
            f"No data for '{symbol}'. Use Yahoo Finance codes (AAPL, ^GSPC, BTC-USD, COMI.CA), "
            "EGX index names (EGX30, EGX33, EGX70) or TradingView codes (EGX:SHARIAH).",
        )

    times = _times(df.index, interval)
    patterns, levels = analyze(df)

    def t(i: int):
        return times[max(0, min(len(times) - 1, int(i)))]

    candles = [
        {"time": times[i], "open": _r(r.Open), "high": _r(r.High), "low": _r(r.Low), "close": _r(r.Close), "volume": float(r.Volume)}
        for i, r in enumerate(df.itertuples())
    ]
    out_patterns = []
    for k, p in enumerate(patterns):
        d = asdict(p)
        d["id"] = k
        d["startTime"], d["endTime"] = t(p.start), t(p.end)
        d["points"] = [{"time": t(i), "index": int(i), "value": _r(v)} for i, v in p.points]
        d["lines"] = [[{"time": t(i), "index": int(i), "value": _r(v)} for i, v in ln] for ln in p.lines]
        out_patterns.append(d)

    return {
        "symbol": resolved,
        "name": meta.get("longName") or meta.get("shortName") or resolved,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "interval": interval,
        "period": period,
        "candles": candles,
        "patterns": out_patterns,
        "levels": [asdict(lv) | {"firstTime": t(lv.first), "lastTime": t(lv.last)} for lv in levels],
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
