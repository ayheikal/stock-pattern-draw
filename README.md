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

## EGX market

`/market` shows the EGX at a glance: EGX 30 / 70 / 100 / 33 Shariah with trend and returns, breadth
(stocks up vs down, share above their 50- and 200-day averages), a sector heatmap (click a sector to
screen it) and the top gainers, losers and most traded stocks. The screener also gets live watchlists:
the 80 most traded EGX stocks and one per sector. Source: TradingView's public screener (unofficial,
delayed; see the note above).

## Track record

`/track` measures whether the outlook has actually worked. For each symbol in a watchlist it replays the
outlook over up to 10 years of history: at every sample bar it rebuilds the outlook from the previous 250 bars
only (no look-ahead), then records what price did over the outlook horizon and whether the trade plan hit its
target or its stop first. Results are grouped by bias (Bullish … Bearish) and compared with a baseline of all
periods, overall and per symbol. Your profile cards show the per-symbol record for the chart's current reading.

Run it from the page (Run button, ~30 s for the EGX list), or from the command line:

```powershell
.venv\Scripts\python -m app.backtest egx 1d
```

Results are cached in `data/backtest_<watchlist>_<interval>.json` (git-ignored). Caveats: the watchlists are
today's large caps (survivorship bias), there are no fees or slippage, and past results don't guarantee future ones.

## My profile (EGX-aware)

`/profile` tracks what you hold and watch, with a Buy / Hold / Sell tag per asset from your own view,
stop and target plus your money rules (not from the chart signals, which the track record shows have no edge).

- **Bonus shares & dividends:** give each holding a *buy date*. Bonus issues and splits after it raise your
  quantity and lower your average cost (e.g. COMI's +10% bonus on 2025-12-17), and dividends are added
  after dividend tax. Data from Yahoo's corporate actions.
- **Costs & taxes:** a per-trade cost % (brokerage + exchange + clearing + stamp duty), dividend tax and gains
  tax. They feed net P/L, a break-even price, position sizing and the Track record ("after costs").
  The defaults are placeholders: copy the real rates from a contract note.
- **Liquidity:** average daily traded value (20 bars). Position size never exceeds your set share of it,
  thin stocks are flagged, and the screener can skip them.
- **Trades:** record each buy and sell (date, qty, price, fees from the contract note or estimated).
  Quantity and average cost then come from the trades (average-cost method), with realized profit,
  fees and dividends per year, and bonus shares applied on their dates.
- **USD view:** EGP holdings are also measured in dollars (USD/EGP on each buy date vs today), so a
  gain that only reflects the pound's fall is visible as such.
- **Order ticket:** Buy / Add more / Trim / Sell tags come with a copy-ready limit order (price rounded to
  your *price step*) to type into your broker app; there's no broker connection.
- **Telegram alerts:** stops here are mental stops, so the app can message you when a stop is hit, a
  target is reached, or an action changes. Checks run Sun–Thu 10:00–14:45 Cairo time while the app is
  running; only new items are sent. Set up under *Telegram alerts* on the profile page.

## Run in the background, phone app, backups

**Start with Windows** (so Telegram alerts work without a terminal):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1           # with phone access
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -NoPhone  # this computer only
powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1         # undo
```

It registers a scheduled task that runs `pythonw -m app.serve` (no window) at sign-in; logs go to
`data\server.log`. If a copy is already running on the port, the new one exits.

**Phone app:** with phone access on, the app also answers on your home Wi-Fi. Every device except this
computer needs the access key: open *My profile → Phone app* and scan the QR code with the phone,
then *Add to Home screen*. The key lives in `data\access_key.txt` (delete it and restart for a new one).
The computer must be on and the app running. Offline copies of pages only work on https/localhost.

**Backups:** every change to the profile is saved as that day's copy in `data\backups` (last 30 days),
and optionally in an extra folder such as OneDrive (*My profile → Backup*), where you can also restore a
copy or download the profile (without the Telegram token).

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
