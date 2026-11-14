"""Read the Market Lens app's own analysis for the trade-picking routine, as short plain text.

    python lens.py market
    python lens.py shortlist [--universe egx_top] [--halal-only] [--watch]
    python lens.py stock COMI.CA [TMGH.CA ...] [--bars 15m]

Read-only: it only calls GET endpoints of the app running on this computer (MARKET_LENS_URL, default
http://127.0.0.1:8000). Standard library only, so any Python runs it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE = os.environ.get("MARKET_LENS_URL", "http://127.0.0.1:8000").rstrip("/")
BREADTH_OK = 50.0  # the app's own line for a healthy market (app/final.py)
GOOD_SECTORS = ("favour", "accumulate")
MARK = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "unknown": "n/a "}


class AppDown(Exception):
    pass


def get(path: str, timeout: int = 300, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail")
        except Exception:
            detail = None
        raise RuntimeError(f"{path}: {detail or e.reason} (HTTP {e.code})") from None
    except (urllib.error.URLError, OSError) as e:
        raise AppDown(str(e)) from None


def num(v, digits: int = 2) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def pct(v) -> str:
    return "-" if v is None else f"{v:+.2f}%"


def yes(v) -> str:
    return "-" if v is None else "yes" if v else "no"


def day(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


# ---- step 1 and 2: the market, then the sectors ----

def mood(index: dict | None, breadth: dict) -> tuple[str, str]:
    """The same rule the app's final decision uses (market_mood in app/final.py)."""
    trend, above = (index or {}).get("trend"), breadth.get("above200")
    if trend == "Uptrend" and (above is None or above >= BREADTH_OK):
        return "STRONG", "Market supports new buys."
    if trend == "Downtrend" and not (above is not None and above >= BREADTH_OK):
        return "WEAK", "Most stocks fall with a falling market: no new buys, or much smaller ones."
    return "MIXED", "No tailwind from the market: be picky, only the best setups."


def market() -> None:
    m = get("/api/market")
    b = m["breadth"]
    egx30 = next((i for i in m["indices"] if i["symbol"] == "EGX30"), None)
    key, text = mood(egx30, b)
    print(f"MARKET  (data as of {day(m.get('asOf'))}; last session {egx30['date'] if egx30 else '-'})")
    print(f"  Mood: {key}. {text}")
    for i in m["indices"]:
        print(f"  {i['name']:<16} {num(i['last']):>12}  day {pct(i['day'])}  week {pct(i['week'])}  month {pct(i['month'])}"
              f"  trend {i.get('trend') or '-'}  above 50d {yes(i.get('above50'))}  above 200d {yes(i.get('above200'))}")
    print(f"  Breadth: {b['advancers']} up, {b['decliners']} down of {b['traded']} traded; "
          f"{num(b['above50'], 0)}% above their 50-day, {num(b['above200'], 0)}% above their 200-day average")

    s = get("/api/sectors")
    print("\nSECTORS  (outlook = financials crossed with price trend; best first)")
    for x in s["sectors"]:
        c, t = x["conclusion"], x.get("technical") or {}
        flag = "*" if c["verdict"] in GOOD_SECTORS else " "
        print(f" {flag} {x['sector']:<26} {c['label']:<15} trend {t.get('trendLabel') or '-':<10} 1m {pct(t.get('perf1m'))}  "
              f"3m {pct(t.get('perf3m'))}  slug {x['slug']}")
    good = [x["sector"] for x in s["sectors"] if x["conclusion"]["verdict"] in GOOD_SECTORS]
    print(f"  * = prefer these: {', '.join(good) or 'none right now'}")
    print("  Each sector has its own watchlist for the shortlist step: --universe egx_sector_<slug>")


# ---- step 3: the shortlist ----

def stance_line(evidence: list[dict]) -> str:
    return "; ".join(f"{e['source']}: {e['reading']}" for e in evidence if e["key"] not in ("plan", "chart"))


def shortlist(universe: str, halal_only: bool, watch: bool) -> None:
    b = get("/api/final/buys", universe=universe)
    rows = b["results"]
    if halal_only:
        rows = [r for r in rows if any(e["key"] == "halal" and e["reading"] == "Halal" for e in r["evidence"])]
    counts = ", ".join(f"{v} {k}" for k, v in sorted(b["counts"].items(), key=lambda kv: -kv[1]))
    print(f"SHORTLIST  watchlist {universe}: {b['checked']} stocks checked ({counts}); data as of {day(b.get('asOf'))}")
    if b.get("mood"):
        print(f"  Market mood: {b['mood']['key']}. {b['mood']['text']}")
    if not rows:
        print("  No stock is a Buy right now" + (" (after the halal filter)." if halal_only and b["results"] else "."))
        print("  That is an answer: the routine ends here unless you only want a watch list (--watch).")
    for i, r in enumerate(rows, 1):
        lv, sz = r.get("levels") or {}, r.get("sizing") or {}
        print(f"  {i}. {r['symbol']}  {r['name']}  [{r['decision']['label']}]  last {num(r['last'])} ({pct(r['changePct'])})")
        print(f"     {r['decision']['reason']}")
        print(f"     entry {num(lv.get('entry'))}  stop {num(lv.get('stop'))}  target {num(lv.get('target'))}  "
              f"reward:risk {num(lv.get('rr'))}  levels from {lv.get('source') or '-'}"
              + (f"  | size {sz.get('shares')} shares" if sz.get("shares") else ""))
        print(f"     {stance_line(r['evidence'])}")
    if watch:
        s = get("/api/screen", universe=universe, show="not-avoid", halal_only="true" if halal_only else None)
        waits = [r for r in s["results"] if r["decision"]["verdict"] == "wait"][:8]
        print(f"\nWATCH LIST  (buy checklist says Wait: close, but not a buy yet; {len(waits)} shown)")
        for r in waits:
            d = r["decision"]
            print(f"  {r['symbol']:<12} {d.get('passed')}/{d.get('known')} checks pass  last {num(r['last'])}  waiting on: {d['reason']}")
        if not waits:
            print("  Nothing is close either.")


# ---- steps 4 to 7: one stock ----

def stock(symbol: str, bars: str) -> None:
    f = get("/api/final", symbol=symbol)
    d, lv = f["decision"], f.get("levels") or {}
    print(f"\n{'=' * 78}\n{f['symbol']}  {f['name']}   last {num(f['last'])} {f.get('currency') or ''} ({pct(f['changePct'])})   "
          f"last bar {f.get('lastBar')}")
    if f["symbol"].upper().endswith(".CA"):
        # The app reads EGX prices from TradingView (codes like EGX:COMI) and falls back to Yahoo (COMI.CA) when that fails.
        print("  WARNING: these prices came from Yahoo, the fallback source. Its history is not always adjusted for splits and\n"
              "  bonus shares, which can put the 200-day average in the wrong place and flip the call. Run it again in a few\n"
              "  minutes; trust the call only when the symbol on the line above starts with EGX:")
    print(f"FINAL CALL: {d['label']}  ({d['group']})")
    print(f"  {d['reason']}")
    if f.get("position"):
        h = f.get("holding") or {}
        print(f"  Already held: {f['position']}, P/L {pct(h.get('plPct'))}, your stop {num(h.get('stop'))}, your target {num(h.get('target'))}")

    print("\nWHAT WENT INTO IT")
    for e in f["evidence"]:
        print(f"  {e['source']:<14} {e['reading']:<20} {e['stance']:<8} ({e['role']})  {e['detail']}")

    print("\nBUY CHECKLIST")
    for c in f["checks"]:
        print(f"  {MARK.get(c['status'], c['status'])}  {c['label']:<30} {c['detail']}")

    print("\nLEVELS AND SIZE")
    if lv.get("entry") and lv.get("stop"):
        print(f"  entry {num(lv['entry'])}  stop {num(lv['stop'])}  target {num(lv.get('target'))}  reward:risk {num(lv.get('rr'))}  "
              f"upside {pct(lv.get('upsidePct'))}  (levels from {lv.get('source')}: 'profile' = yours, 'chart' = the app's)")
        try:
            z = get("/api/size", entry=lv["entry"], stop=lv["stop"], currency=f.get("currency") or "")
            if z.get("configured") and z.get("ok"):
                print(f"  size: {z['shares']} shares, costing {num(z['cost'])} ({num(z['costPct'], 1)}% of capital), "
                      f"risking {num(z['risk'])} ({num(z['riskPct'])}% of capital) if the stop is hit; limited by {z.get('limitedBy')}")
                print("  (sized on its own. If the checklist's 'Fits my money rules' line gives fewer shares, use that one: "
                      "it also applies your cash, total-risk and sector limits)")
            else:
                print(f"  size: not available ({z.get('limitNote') or 'set capital and risk per trade in My profile'})")
        except RuntimeError as e:
            print(f"  size: not available ({e})")
    else:
        print("  No stop / target to judge: set your own in My profile.")
    if f.get("ticket"):
        print(f"  order ticket: {json.dumps(f['ticket'], ensure_ascii=False)}")

    print(f"\nENTRY TIMING  ({bars} bars; only for a trade already decided)")
    try:
        t = get("/api/intraday", symbols=symbol, interval=bars, show="all")
        r = t["results"][0] if t["results"] else None
        if r is None:
            print("  No intraday data for this symbol.")
        else:
            marks = "  ".join(f"{c['label']} {'yes' if c['ok'] else 'n/a' if c['ok'] is None else 'no'}" for c in r["checks"])
            print(f"  {r['passed']}/5 rules pass on the bar of {r['lastBar']}: {marks}")
            print(f"  vs VWAP {pct(r.get('vwapPct'))}, RSI {num(r.get('rsi'), 1)}, relative volume {num(r.get('rvol'))}x, "
                  f"ATR {num(r.get('atrPct'))}% of price")
    except RuntimeError as e:
        print(f"  not available ({e})")
    print(f"\n  {f.get('note', '')}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    ap = argparse.ArgumentParser(description="Market Lens trade-picking routine, read-only")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("market", help="market mood and sector outlook")
    s = sub.add_parser("shortlist", help="stocks the app's final decision calls a Buy")
    s.add_argument("--universe", default="egx_top", help="watchlist key (see /api/universes); default the most traded EGX stocks")
    s.add_argument("--halal-only", action="store_true")
    s.add_argument("--watch", action="store_true", help="also list the stocks closest to a buy (checklist says Wait)")
    k = sub.add_parser("stock", help="everything about one or more stocks: final call, checklist, levels, size, entry timing")
    k.add_argument("symbols", nargs="+")
    k.add_argument("--bars", default="15m", choices=["5m", "15m", "1h"])
    a = ap.parse_args()
    try:
        if a.cmd == "market":
            market()
        elif a.cmd == "shortlist":
            shortlist(a.universe, a.halal_only, a.watch)
        else:
            for sym in a.symbols:
                try:
                    stock(sym, a.bars)
                except RuntimeError as e:
                    print(f"\n{sym}: {e}")
    except AppDown as e:
        print(f"The Market Lens app is not answering at {BASE} ({e}).\n"
              "Start it, then run this again:  Start-ScheduledTask -TaskName MarketLens   "
              "(or, from the project folder:  .venv\\Scripts\\python -m app.serve)")
        return 2
    except RuntimeError as e:
        print(f"The app refused the request: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
