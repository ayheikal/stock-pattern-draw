# Market Lens

A personal investing workstation: a buy checklist per stock, pattern charts with an outlook and trade plan, an EGX
market overview, sector outlook, a halal-aware screener, your portfolio with costs, taxes, portfolio-wide limits and
a USD view, Telegram alerts, and a track record that checks whether the signals work. Runs locally, with a phone app over home Wi-Fi.

## Charts and patterns

Type a stock, index or asset code and get a candlestick chart with classical
technical-analysis patterns detected and drawn automatically.

- **Reversal:** Head & Shoulders, Inverse H&S, Double/Triple Top & Bottom (neckline + measured-move target)
- **Continuation:** Ascending/Descending/Symmetrical Triangles, Rising/Falling Wedges, Rectangles, Bull/Bear Flags & Pennants
- **Broadening:** Broadening Formation (megaphone: higher highs and lower lows, breaks either way), Ascending Broadening Wedge (bearish), Descending Broadening Wedge (bullish); target = the pattern's widest point
- **Trend:** active up/down trendlines, ascending/descending channels
- **Support / Resistance:** horizontal levels clustered from swing points
- **Candlesticks:** Doji, Hammer, Inverted Hammer, Hanging Man, Shooting Star, Engulfing, Harami, Morning/Evening Star, Three White Soldiers / Black Crows
- **Outlook:** a plain-language expectation for the next period (next week on daily bars, next month on weekly, …): overall bias, the main signal, bullish/bearish scenarios with trigger levels and targets, and an ATR-based typical range
- **Trade plan:** entry, target and stop-loss with reward/risk. The stop sits just beyond the main pattern's last swing (its invalidation point), else beyond the nearest support/resistance, else 2 × ATR. A neutral outlook gives two conditional breakout/breakdown plans instead. Target and stop are drawn on the chart.

## Buy checklist

One verdict per stock (**Ready to buy**, **Wait**, **Avoid** or **Not enough data**) with the reason, from checks
that each pass, warn, fail or have no data ([app/decision.py](app/decision.py)). It's on the chart page, ranks the screener,
and decides whether My profile says Buy / Add more.

| Check | Pass | Warn | Fail |
|---|---|---|---|
| Above its 200-day average | above | | below: don't buy into a long-term downtrend |
| Growth beats inflation | EPS and revenue growth above your inflation rate | one of them | EPS shrinking and revenue below inflation |
| Profitable (ROE) | ROE above inflation | ROE positive but below | ROE ≤ 0 |
| Price vs sector (P/E) | at or below the sector median | up to 2× | above 2×, or no earnings |
| Fundamental score | Strong (75%+ on the scorecard below) | Average (50–74%) | Weak (below 50%) |
| Sector outlook | Favour / Accumulate (Sectors page) | Watch, Neutral, Caution | Avoid |
| Reward vs risk | 2 : 1 or better | 1–2 : 1 | below 1 : 1 with *your* levels (the chart's levels only warn) |
| Liquid enough | daily value ≥ your minimum | thin | |
| No results due soon | | results within your blackout days (→ Wait) | |
| Data up to date | | no new price for 3+ sessions (→ Wait) | |
| Fits my money rules | sized normally, or capped by a size rule | cut by cash, total risk or sector already held; no room (→ Wait) | |
| Halal | on your Shariah list or tagged halal | tagged not halal | never: halal is a flag |

Any fail gives **Avoid**. Three or more of the core checks (trend, growth, ROE, P/E vs sector, sector outlook, reward vs
risk) with no data give **Not enough data**. Fundamentals come from TradingView's EGX screener, and from Yahoo Finance for
other stocks; those have no EGX sector, so their P/E-vs-sector and sector checks stay unknown, and their growth and ROE
are judged against 3% inflation instead of your EGP rate. A Wait check or two or more warnings give
**Wait**. Otherwise it's **Ready to buy**. Reward vs risk uses your target and stop from My profile when set, else the chart's.

The chart outlook is **not** a check. The Track record shows it has no reliable edge, so it's shown next to the
checklist for reference only. The fundamental checks can't be backtested (there's no history of past
fundamentals), so treat the checklist as discipline, not a forecast.

The **screener** ranks a watchlist Ready first, then Wait, then Not enough data; within each, more passed checks first,
then the more liquid stock. *Show* lists Ready only, everything but Avoid (the default) or everything, and says how
many were hidden. Its Target / Stop / R:R columns are the levels the checklist judged, measured from the last price.

`GET /api/decision?symbol=COMI.CA&interval=1d` returns the checks, verdict and the levels it judged, plus the rating below.

## Fundamental + technical rating

Next to the buy checklist ([app/rating.py](app/rating.py)): two scorecards and one signal, **Strong buy**, **Buy**,
**Hold**, **Reduce** or **Sell**. It's on the chart page (with every figure), as a *Rating* column in the screener, and on
each profile card. Each figure is Strong / OK / Weak (2 / 1 / 0 points), or not scored when it isn't reported; a scorecard
is its points as a % of those available.

- **Fundamentals** (needs 5+ figures): valuation (P/E, P/B and EV/EBITDA against the sector median when 3+ companies report
  them, PEG on growth *after inflation*, free cash flow yield), profitability (ROE and ROIC against inflation, operating and
  net margin against the sector median), growth (revenue and EPS against inflation), debt (debt-to-equity, net debt /
  EBITDA), liquidity (current and quick ratio), cash flow (operating cash flow ÷ net income, FCF margin) and dividends
  (payout ratio; the yield is shown, not scored). Strong at 75%+, Average from 50%, Weak below. Banks, brokers, insurers and
  other lenders skip the debt, liquidity and cash-flow ratios, since debt is their raw material. Red flags (shrinking revenue,
  weak cash conversion, heavy debt, negative equity, returns below inflation, a payout over 100%…) are listed whatever the score.
- **Technicals** (needs 4+ checks, daily or weekly bars): above the 200-day and 50-day averages, 50-day above 200-day,
  200-day average rising, 6-month momentum, RSI and distance from the 52-week high (40 / 10 weeks etc. on weekly bars).
  Uptrend at 65%+, Downtrend at 35% or less, else Mixed. Chart patterns aren't part of it.
- **Signal** = fundamentals × trend: Strong + Uptrend gives *Strong buy*, Strong + Downtrend *Watch for a turn*, Average +
  Uptrend *Buy (trend-led)*, Average + Downtrend or Weak + Mixed *Reduce*, and Weak + Downtrend *Sell*.

The rating summarizes; the checklist decides. A stock can be *Ready to buy* while rated *Hold*: the checklist adds reward vs
risk, liquidity, results dates and your money rules. In My profile, a holding rated **Sell** shows under *Needs attention*
and is sent as a Telegram alert, but your own stop and target still set its Buy / Hold / Sell tag. Fundamentals can't be
backtested (there's no history of past figures), and TradingView leaves many EGX figures blank, so check *figures scored*.

## Below fair value

`/value` lists EGX stocks priced under what their peers' multiples say they're worth ([app/valuation.py](app/valuation.py)).
Each stock gets a fair price from the median P/E, P/B and (except banks and other lenders) EV/EBITDA of its peers: its
industry when 3+ companies there report the figure, else its sector. Fair value is the median of these estimates. It needs at
least two, and no single estimate can be more than 3× above or below the price. A stock is listed when its price is
at least your *margin of safety* (default 20%) under its fair value.

Cheap isn't the same as a chance to buy, so each listed stock also gets the fundamental scorecard and its trend:

| Verdict | Means |
|---|---|
| **Buy zone** | cheap, Average or Strong fundamentals, above its 200-day average |
| **Watch for a turn** | cheap and sound, but below its 200-day average (or too new to tell) |
| **Check the figures** | cheap, but too few figures to grade the business |
| **Cheap for a reason** | cheap, but losing money or Weak fundamentals |

Filter by verdict, liquidity (value traded in the latest session), sector and halal; click a row for its chart and buy
checklist. This is relative value: an overpriced sector's cheapest stock can still be dear, and a year's profit may include
one-offs. Only stocks with at least two reported multiples are valued, which is often a minority of the EGX.
`GET /api/value?margin=20` returns the list.

## Final decision

`/decide` combines every analysis into one call per stock ([app/final.py](app/final.py)): **Strong buy**, **Buy**,
**Wait**, **Don't buy** or **Not enough data** for a stock you don't hold; **Add more**, **Hold**, **Reduce** or **Sell**
(or your plan's own *Cut loss*, *Take profit*, *Trim*…) for one you do. Each input is shown with what it says, whether it
counts for or against, and its role:

| Role | Inputs |
|---|---|
| Decides | Your plan in My profile (a held stock's stop, target, view and size rules come first), the buy checklist |
| Weighs | The rating, fair value against peers (EGX only), market mood (EGX 30 trend + share of stocks above their 200-day) |
| Context | The chart outlook and its track record on the stock (no reliable edge, so no vote), the halal tag |

- **Not held:** checklist Avoid, rating Sell or your own *not halal* tag → Don't buy. Checklist Wait / Not enough data → the
  same. Checklist Ready → each weighing input for counts +1, against −1: 2 or more is a Strong buy, 0–1 a Buy, below 0 Wait.
- **Held:** your plan says sell → do that. Rating Sell → Sell. Two or more against (checklist, rating, fair value, market,
  halal) → Reduce. Your plan says Add more and nothing is against → Add more. Otherwise Hold.

Pick a stock from the My profile chips or type any ticker; `/decide?symbol=COMI.CA` links straight to one.
`GET /api/final?symbol=COMI.CA&interval=1d` returns the call and every input. Like the checklist, it can't be backtested.

## Run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 (or deep-link: `http://localhost:8000/?symbol=COMI.CA&interval=1d&period=1y`).

Tests: `.venv\Scripts\python -m pytest`

`requirements.txt` holds what the app needs to run; `requirements-dev.txt` adds the test tools.

### Docker

Requires Docker Desktop (or Docker Engine with the Compose plugin).

```powershell
docker compose up -d --build     # build and start in the background
docker compose logs -f           # follow the log
docker compose down              # stop
```

Open http://localhost:8000. The container restarts with Docker, so Telegram alerts keep running without a terminal.
Your `data\` folder is mounted into the container, so the profile, backups, backtest results and alert state are the
same ones the non-Docker app uses. Run only one of the two at a time, since both use port 8000.

Limits in Docker:

- **This computer only.** The port is published on `127.0.0.1`. Phone mode isn't supported: inside a container
  every request comes from Docker's network rather than `127.0.0.1`, so the app can't tell this computer from a phone.
  Use the Windows auto-start below for phone access.
- **Extra backup folder:** a path such as OneDrive set under *My profile → Backup* isn't visible inside the container.
  Daily copies still go to `data\backups`.

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

EGX stocks (`.CA` or a bare code Yahoo doesn't know) and EGX indices come from
**TradingView** ([app/tradingview.py](app/tradingview.py)): Yahoo publishes EGX
sessions a day or more late and has no history for EGX indices. Yahoo Finance serves
everything else, and is the fallback for an EGX stock TradingView can't find.

> ⚠️ The TradingView source uses its unofficial, undocumented chart websocket.
> TradingView's terms don't permit this kind of automated collection, and it can
> change, rate-limit or block without notice. Fine for personal/local use; for a
> public site, switch to a licensed feed behind `fetch_ohlcv` in [app/data.py](app/data.py).

TradingView history depth varies by index: EGX30 goes back to 1998, while
EGX 33 Shariah starts in 2022.

## EGX market

`/market` shows the EGX at a glance: EGX 30 / 70 / 100 / 33 Shariah and the USD/EGP rate with trend and returns, breadth
(stocks up vs down, share above their 50- and 200-day averages), a sector heatmap (click a sector to
screen it) and the top gainers, losers and most traded stocks. The screener also gets live watchlists:
the 80 most traded EGX stocks and one per sector. Source: TradingView's public screener (unofficial,
delayed; see the note above).

## Sector outlook

`/sectors` rates each EGX sector **Growth**, **Normal** or **Shallow** (weak / shrinking) from company financials
and upcoming events, not chart patterns. For each sector the app takes the median company's revenue and
earnings-per-share growth (yearly, else the latest quarter) **after inflation**, and the share of companies whose
earnings beat inflation. Growth needs two of these clearly positive, Shallow two clearly negative; anything else is
Normal. Set the inflation rate on the page (12% is a placeholder). Each card also shows ROE, P/E and dividend yield,
the largest companies, and upcoming results dates (next 90 days).

Add your own view per sector: a status that overrides the suggestion, a note on why, and future events such as a
rate decision, a new law or a big contract, each marked positive, negative or neutral. They're saved in your profile,
so backups and the profile download include them. Many EGX companies don't report these figures to TradingView's
screener, so a sector with growth figures for fewer than 3 companies shows *Not enough data*.

Each sector ends with a **conclusion** that mixes the financial status with the price trend: the share of its stocks
above their 50- and 200-day averages and its size-weighted 3-month move give Uptrend / Mixed / Downtrend. Good
financials in an uptrend give *Favour*, and weak financials in a downtrend give *Avoid*. Mixed cases come out as
*Accumulate*, *Watch*, *Neutral* or *Caution*. For example, good financials with falling prices give
"Watch for a turn". The conclusion also notes an overbought or oversold median RSI and whether your own upcoming
events lean positive or negative. Sectors are listed best conclusion first.

## Halal (Shariah-compliant) tag

Stocks get a **Halal** tag on the chart, in the screener, on the Market page and in My profile. TradingView doesn't
publish who is in the EGX 33 Shariah index, so the list comes from you: in My profile → *Halal (Shariah) list*, paste
the current members from EGX's website, either as codes (`ABUK.CA`, `EGX:ABUK`, `ABUK`) or as the whole constituents table
(ISINs are matched against the live EGX list). EGX reviews the index every six months, and a list older than that is
flagged as possibly out of date.

A stock that isn't on the list isn't tagged *Not halal*, since the index only holds the most liquid compliant stocks.
Tag any stock yourself from its chart (*Mark halal* / *Mark not halal*); your tag wins over the list. The screener has a
**Halal only** filter and a *Halal: Shariah list + my tags* watchlist. All of it is saved in your profile and backups.

## Track record

`/track` measures whether the outlook has actually worked. For each symbol in a watchlist it replays the
outlook over up to 10 years of history: at every sample bar it rebuilds the outlook from the previous 250 bars
only (no look-ahead), then records what price did over the outlook horizon and whether the trade plan hit its
target or its stop first. Results are grouped by bias (Bullish … Bearish) and compared with a baseline of all
periods, overall and per symbol. Your profile cards show the per-symbol record for the chart's current reading, over all history and over the last year (from the newest run).

Run it from the page (Run button, ~30 s for the EGX list), or from the command line:

```powershell
.venv\Scripts\python -m app.backtest egx 1d
```

Results are cached in `data/backtest_<watchlist>_<interval>.json` (git-ignored). Caveats: the watchlists are
today's large caps (survivorship bias), there are no fees or slippage, and past results don't guarantee future ones.

## My profile (EGX-aware)

`/profile` tracks what you hold and watch, with a Buy / Hold / Sell tag per asset from your own view,
stop and target, your money rules and the buy checklist (not from the chart signals, which the track record shows have no edge).

- **Bonus shares & dividends:** give each holding a *buy date*. Bonus issues and splits after it raise your
  quantity and lower your average cost (e.g. COMI's +10% bonus on 2025-12-17), and dividends are added
  after dividend tax. Data from Yahoo's corporate actions.
- **Costs & taxes:** a per-trade cost % (brokerage + exchange + clearing + stamp duty), dividend tax and gains
  tax. They feed net P/L, a break-even price, position sizing and the Track record ("after costs").
  The defaults are placeholders: copy the real rates from a contract note.
- **Liquidity:** average daily traded value (20 bars). Position size never exceeds your set share of it,
  thin stocks are flagged, and the screener can skip them.
- **Portfolio limits:** a new buy is cut to fit your **cash**, your **max loss on all positions together** (if every
  stop is hit at once; default 6%) and your **max per sector** (default 35%), and the binding rule is named. *Capital*
  is the money you put in: cash = capital + realized gains + dividends − what you paid for what you hold. The Risk tile
  shows your weight per sector, and going over either limit is flagged.
- **Buy checklist and results dates:** *Buy* and *Add more* also need the buy checklist to say Ready (or have too
  little data to judge), and no results within your blackout days (default 10). Holdings with results coming up, or with
  no new price for 3+ sessions, are flagged and sent as Telegram alerts.
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
  data.py              provider routing (TradingView for EGX, Yahoo otherwise), aliases, 5-min cache
  tradingview.py       TradingView websocket client (EGX indices)
  outlook.py           next-period expectation built from patterns, trend, RSI and levels
  pivots.py            ATR + swing-point (zigzag) detection shared by all detectors
  patterns/
    reversal.py        H&S, double/triple tops & bottoms
    continuation.py    triangles, wedges, rectangles, channels, flags, pennants
    levels.py          S/R clustering, trendlines
    candles.py         candlestick rules (only in trend context)
    __init__.py        analyze(): runs all detectors + overlap suppression
  final.py             final decision: combines my plan, checklist, rating, fair value and market mood
  main.py              FastAPI: GET /api/analyze?symbol=&interval=&period=
static/                Frontend (TradingView Lightweight Charts)
```

Detection is rule-based: swings smaller than 1×ATR are ignored, shapes are
validated with tolerances expressed in ATR units, and each pattern gets a
confidence score (use the *Min confidence* slider to filter). Reversal patterns
and flags whose breakout never happened are discarded as failed; ones still
within their breakout window are shown as *forming*.

*For education only — not financial advice. Data may be delayed.*
