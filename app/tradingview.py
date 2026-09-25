"""OHLCV history from TradingView's chart websocket.

Unofficial and undocumented: used only as a fallback for symbols Yahoo cannot
serve (e.g. EGX indices). It may change or be rate-limited without notice.
"""
from __future__ import annotations

import json
import random
import re
import string

import pandas as pd
import websocket

WS_URL = "wss://data.tradingview.com/socket.io/websocket"
RESOLUTIONS = {"1h": "60", "1d": "D", "1wk": "W", "1mo": "M"}
# Approximate bars per period, per interval (hourly assumes ~5 trading hours/day).
PERIOD_DAYS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520, "max": 10000}
BARS_PER_DAY = {"1h": 5, "1d": 1, "1wk": 1 / 5, "1mo": 1 / 21}
MAX_BARS = 5000

_FRAME = re.compile(r"~m~\d+~m~")


class TradingViewError(Exception):
    pass


def _frame(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def _msg(method: str, params: list) -> str:
    return _frame(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def bar_count(interval: str, period: str) -> int:
    return max(30, min(MAX_BARS, int(PERIOD_DAYS[period] * BARS_PER_DAY[interval])))


def fetch_tv(symbol: str, interval: str = "1d", period: str = "1y", timeout: float = 15) -> tuple[pd.DataFrame, dict]:
    """Fetch bars for an 'EXCHANGE:SYMBOL' code. Returns (dataframe, metadata)."""
    session = "cs_" + "".join(random.choices(string.ascii_lowercase, k=12))
    ws = websocket.create_connection(
        WS_URL, origin="https://www.tradingview.com", timeout=timeout, header=["User-Agent: Mozilla/5.0"]
    )
    try:
        for m, p in [
            ("set_auth_token", ["unauthorized_user_token"]),
            ("chart_create_session", [session, ""]),
            ("resolve_symbol", [session, "sds_sym_1", "=" + json.dumps({"symbol": symbol, "adjustment": "splits"})]),
            ("create_series", [session, "sds_1", "s1", "sds_sym_1", RESOLUTIONS[interval], bar_count(interval, period), ""]),
        ]:
            ws.send(_msg(m, p))

        info: dict = {}
        bars: list = []
        done = False
        while not done:
            for part in _FRAME.split(ws.recv()):
                if not part:
                    continue
                if part.startswith("~h~"):  # heartbeat must be echoed
                    ws.send(_frame(part))
                    continue
                msg = json.loads(part)
                method = msg.get("m")
                if method == "symbol_resolved":
                    info = msg["p"][2]
                elif method == "timescale_update":
                    bars = msg["p"][1].get("sds_1", {}).get("s", bars)
                elif method in ("symbol_error", "series_error", "critical_error"):
                    raise TradingViewError(f"{symbol}: {method}")
                elif method == "series_completed":
                    done = True
    except (websocket.WebSocketException, OSError) as e:
        raise TradingViewError(f"{symbol}: {e}") from e
    finally:
        ws.close()

    if not bars:
        return pd.DataFrame(), {}
    rows = [b["v"] for b in bars if len(b["v"]) >= 5]
    df = pd.DataFrame(
        [r[:6] + [0.0] * (6 - len(r)) for r in rows], columns=["ts", "Open", "High", "Low", "Close", "Volume"]
    )
    tz = info.get("timezone") or "UTC"
    df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True).dt.tz_convert(tz)
    df.index.name = None
    meta = {
        "longName": info.get("description"),
        "currency": info.get("currency_code"),
        "fullExchangeName": f"{info.get('exchange', '')} · via TradingView".strip(" ·"),
    }
    return df, meta
