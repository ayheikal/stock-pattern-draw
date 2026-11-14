---
name: pick-trade
description: Walk the Market Lens trade-picking routine (market mood, sectors, shortlist, fair value, buy checklist, position size, entry timing) and end with either a concrete trade plan or a clear "no trade today". Use this whenever the user asks what to buy or trade, wants a ticker picked, asks for today's shortlist or watch list, asks "should I buy X" / "is X a buy" / "check X for me" about an EGX stock or any symbol the app covers, or asks to run the routine or scenario in this project, even if they don't name the app or its tabs.
---

# Pick a trade with Market Lens

Market Lens (this repository) already does the analysis: market mood, sector outlook, a buy checklist, a
fundamental + technical rating, fair value, a final decision per stock, position sizing and an intraday
timing check. This skill walks those in a fixed order, top-down, and turns them into one answer.

Your job is to **read the app's verdicts and report them faithfully**, not to form your own view of the stock.
The user built the app to keep their trading disciplined; an assistant that overrides "Don't buy" with its own
optimism defeats the point. Where you add judgement, it is in choosing among the app's Buy calls and in saying
plainly what is uncertain.

## How to get the data

Everything comes from the bundled script, which calls the app running on this computer and prints short text:

```
python .claude/skills/pick-trade/scripts/lens.py market
python .claude/skills/pick-trade/scripts/lens.py shortlist [--universe egx_top] [--halal-only] [--watch]
python .claude/skills/pick-trade/scripts/lens.py stock SYMBOL [SYMBOL ...] [--bars 15m]
```

- It is read-only (GET requests only). `shortlist` and `stock` take 10 to 60 seconds: the app fetches live data.
- If it says the app is not answering, start it (`Start-ScheduledTask -TaskName MarketLens`, or
  `.venv\Scripts\python -m app.serve` from the project folder), wait a few seconds and retry.
- Symbols are typed as on the EGX with `.CA` (`COMI.CA`); the app answers with its own code (`EGX:COMI`). Both work.
- Watchlists: `egx_top` (the most traded EGX stocks, the default), `egx` (indices and large caps),
  `egx_shariah` (the user's halal list), `egx_sector_<slug>` (one sector; slugs are printed by `market`), `us`, `world`.

## The routine

Two entry points. If the user names a stock ("should I buy COMI?"), run step 1 for context and jump to step 4.
If they ask what to buy, run all of it.

**1. Market: is this a day to buy at all?** Run `market`. The Mood line is the gate:
- WEAK: say so and stop, unless the user only wants to review what they hold. Buying into a falling market is the
  commonest way the rest of the routine gets wasted.
- MIXED: continue, but hold candidates to a higher standard (see step 5).
- STRONG: continue.

**2. Sectors: where is the strength?** From the same output, note the sectors marked `*` (Favour / Accumulate) and
the ones marked Caution or Avoid. Candidates from starred sectors rank first; ones from Avoid sectors need a
strong reason to stay on the list.

**3. Shortlist.** Run `shortlist` (add `--halal-only` if the user trades halal stocks only; the app tracks a halal list,
so ask once if you don't know). This is the app's final decision over a whole watchlist, strongest call first.
- If it lists Buy calls, take the top three to five, preferring starred sectors.
- If it says no stock is a Buy, **that is the result**. Report "no trade today" as a normal outcome, not a failure.
  Offer the watch list (`--watch`: stocks the checklist says Wait on, with what each is waiting for), and make
  clear a watch list is things to keep an eye on, not things to buy.
- To look deeper into a strong sector, rerun with `--universe egx_sector_<slug>`.

**4. One stock at a time.** Run `stock` on each survivor (several symbols in one call is fine). It prints the final
call, every input behind it, the full buy checklist, levels, size and entry timing. Read in this order:
- *Final call.* Anything outside the Buy group (Wait, Don't buy, Hold, Reduce, Sell) ends it for a new purchase.
  Report the reason the app gives.
- *Fair value* (in "What went into it"). "Above fair value" on a Buy call is worth flagging: the margin for error
  is thin.
- *Buy checklist.* Every FAIL and WARN gets a mention, in the user's terms. A results date inside the blackout
  window means wait until the results are out, whatever else passes.

**5. Levels and size.** A trade plan needs entry, stop, target and share count.
- Reward:risk under 2 is a reason to pass or to wait for a better entry. In a MIXED market, insist on it.
- Levels marked `chart` are the app's suggestion; `profile` means the user set their own, which take priority.
- For the share count use the smaller of the "size" line and the checklist's "Fits my money rules" line: the
  checklist one also applies the user's cash, total-risk and sector limits.

**6. Entry timing.** The last block scores five intraday rules. Use it only to time a purchase already decided:
4 or 5 of 5 means today is a reasonable moment; below VWAP with weak volume means wait for a better moment or
another day. Never present it as a reason to buy. The app's own backtest found these rules lose money after
costs when traded on their own on EGX stocks, so they are a timing aid and nothing more. Outside market hours
the reading is the last session's final bar; say so.

**7. After the decision.** Tell the user the follow-up in the app: add the stock in My profile with the stop and
target (that produces the order ticket and applies the portfolio limits), set a price alert at the stop on the
chart page, keep Telegram alerts on. Stops in this app are mental: the broker does not see them.

## What to hand back

Lead with the outcome in one line: either the ticker(s) to trade, or "no trade today" and why. Then, for each
stock that survived:

```
<SYMBOL>  <name>
Call: <app's final call> — <app's reason>
Plan: buy <shares> at about <entry>, stop <stop>, target <target> (reward:risk <x>)
Risk: <amount and % of capital if the stop is hit>
For: <the inputs that support it>
Against / watch: <every FAIL and WARN, results date, fair-value caveat>
Timing: <n>/5 intraday rules; <enter now / wait>
```

Then one short line each on the market mood and the favoured sectors, and the stocks that were dropped with the
single reason each was dropped. Keep the user's attention on the decision: no tables of raw indicator values
unless they ask.

When the answer is "no trade today", say what would change it (for example: "ORAS needs its sector outlook to
improve", "EFIH reports results on the 18th"), drawn from the watch list.

## Honesty rules

These are here because the user will act on the answer with real money.

- Quote the app's verdicts as they are. Do not upgrade a Wait to a Buy, and do not invent levels, fair values
  or dates the script did not print.
- Say what is not proven: the buy checklist and final decision are discipline, not a tested edge; the app's
  Track record page shows the chart outlook has no reliable edge.
- Data can be stale or late. Print the "last bar" date, and say when the market is closed.
- Check which source answered. The app reads EGX prices from TradingView (the symbol comes back as `EGX:COMI`) and
  silently falls back to Yahoo (`COMI.CA`) when that fails. Yahoo's history is not always adjusted for splits and
  bonus shares, and that has flipped a Buy to a Don't buy on the same closing price. When `stock` prints its Yahoo
  warning, rerun after a few minutes, and if the two sources disagree, report both calls and say which data each
  came from rather than picking one silently.
- You do not place orders, and you do not write to the user's profile unless they ask you to in so many words.
  The script cannot do either.
- This is a routine for applying the user's own rules, not financial advice; say so once, briefly, at the end.
