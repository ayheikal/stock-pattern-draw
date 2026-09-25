# Pattern Charts

Type a stock, index or asset code and get a candlestick chart with classical
technical-analysis patterns detected and drawn automatically.

- **Reversal:** Head & Shoulders, Inverse H&S, Double/Triple Top & Bottom (neckline + measured-move target)
- **Continuation:** Ascending/Descending/Symmetrical Triangles, Rising/Falling Wedges, Rectangles, Bull/Bear Flags & Pennants
- **Trend:** active up/down trendlines, ascending/descending channels
- **Support / Resistance:** horizontal levels clustered from swing points
- **Candlesticks:** Doji, Hammer, Inverted Hammer, Hanging Man, Shooting Star, Engulfing, Harami, Morning/Evening Star, Three White Soldiers / Black Crows
- **Outlook:** a plain-language expectation for the next period (next week on daily bars, next month on weekly, …): overall bias, the main signal, bullish/bearish scenarios with trigger levels and targets, and an ATR-based typical range
- **Trade plan:** entry, target and stop-loss with reward/risk. The stop sits just beyond the main pattern's last swing (its invalidation point), else beyond the nearest support/resistance, else 2 × ATR. A neutral outlook gives two conditional breakout/breakdown plans instead. Target and stop are drawn on the chart.

## Run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 (or deep-link: `http://localhost:8000/?symbol=COMI.CA&interval=1d&period=1y`).

Tests: `.venv\Scripts\python -m pytest`

## Symbols

Codes follow Yahoo Finance:

| Market | Examples |
|---|---|
| US stocks | `AAPL`, `MSFT`, `TSLA` |
| Indices | `^GSPC` (or `SPX`), `^NDX`, `^DJI`, `^GDAXI` (or `DAX`) |
| Egyptian Exchange (EGX) stocks | `COMI.CA`, `TMGH.CA`, `ETEL.CA` — a bare `COMI` also works (`.CA` is tried automatically) |
| EGX indices | `EGX30`, `EGX33` (or `EGX 33 Shariah`), `EGX70`, `EGX100`, `EGX30CAPPED` |
| Any TradingView code | `EGX:SHARIAH`, `EGX:EGX30`, … (`EXCHANGE:SYMBOL`) |
| Crypto / FX / commodities | `BTC-USD`, `EURUSD=X`, `GC=F` |

### Data sources

Yahoo Finance is the primary source. Yahoo serves only a live quote for EGX
indices (no history), so those — and any `.CA` or bare code Yahoo can't serve —
are fetched from **TradingView** instead ([app/tradingview.py](app/tradingview.py)).

> ⚠️ The TradingView source uses its unofficial, undocumented chart websocket.
> TradingView's terms don't permit this kind of automated collection, and it can
> change, rate-limit or block without notice. Fine for personal/local use; for a
> public site, switch to a licensed feed behind `fetch_ohlcv` in [app/data.py](app/data.py).

TradingView history depth varies by index: EGX30 goes back to 1998, while
EGX 33 Shariah starts in 2022.

## How it works

```
app/
  data.py              provider routing (Yahoo → TradingView), aliases, 5-min cache
  tradingview.py       TradingView websocket client (EGX indices)
  outlook.py           next-period expectation built from patterns, trend, RSI and levels
  pivots.py            ATR + swing-point (zigzag) detection shared by all detectors
  patterns/
    reversal.py        H&S, double/triple tops & bottoms
    continuation.py    triangles, wedges, rectangles, channels, flags, pennants
    levels.py          S/R clustering, trendlines
    candles.py         candlestick rules (only in trend context)
    __init__.py        analyze(): runs all detectors + overlap suppression
  main.py              FastAPI: GET /api/analyze?symbol=&interval=&period=
static/                Frontend (TradingView Lightweight Charts)
```

Detection is rule-based: swings smaller than 1×ATR are ignored, shapes are
validated with tolerances expressed in ATR units, and each pattern gets a
confidence score (use the *Min confidence* slider to filter). Reversal patterns
and flags whose breakout never happened are discarded as failed; ones still
within their breakout window are shown as *forming*.

*For education only — not financial advice. Data may be delayed.*
