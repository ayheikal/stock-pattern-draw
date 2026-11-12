/* global LightweightCharts */
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    form: $("search"), symbol: $("symbol"), interval: $("interval"), period: $("period"),
    chart: $("chart"), status: $("status"), title: $("title"), subtitle: $("subtitle"),
    legend: $("legend"), list: $("patternList"), levels: $("levelList"), count: $("count"),
    minConf: $("minConf"), minConfOut: $("minConfOut"), theme: $("theme"), showMode: $("showMode"),
  };
  try { const v = localStorage.getItem("showMode"); if (v) els.showMode.value = v; } catch (_) {}

  const state = { data: null, selected: null, overlays: [], priceLines: [] };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- chart setup ----------
  const chart = LightweightCharts.createChart(els.chart, {
    autoSize: true,
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderVisible: false },
    leftPriceScale: { borderVisible: false, visible: false },
    timeScale: { borderVisible: false, rightOffset: 8 },
  });
  const candles = chart.addCandlestickSeries({ borderVisible: true, priceLineVisible: false });
  // EGX charts: the close in US dollars, on its own axis on the left (dollars are ~1/50 of the EGP price).
  const usd = chart.addLineSeries({ priceScaleId: "left", lineWidth: 1.5, priceLineVisible: false, crosshairMarkerVisible: false,
    title: "USD", priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
  const sma = chart.addLineSeries({ lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false, title: "SMA 200" });
  const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  // ---------- indicators ----------
  // Worked out here from the closes, starting in the server's warm-up closes so EMA / RSI / MACD read as
  // they would on a longer chart. Each array lines up with the candles; null where it isn't defined yet.
  function smaOf(v, n) {
    const out = new Array(v.length).fill(null);
    let sum = 0;
    v.forEach((x, i) => {
      sum += x - (i >= n ? v[i - n] : 0);
      if (i >= n - 1) out[i] = sum / n;
    });
    return out;
  }
  function emaOf(v, n) { // seeded with the simple average of the first n values; nulls at the start are skipped
    const out = new Array(v.length).fill(null), k = 2 / (n + 1), start = v.findIndex((x) => x != null);
    if (start < 0 || v.length - start < n) return out;
    let e = v.slice(start, start + n).reduce((a, b) => a + b, 0) / n;
    out[start + n - 1] = e;
    for (let i = start + n; i < v.length; i++) out[i] = e = v[i] * k + e * (1 - k);
    return out;
  }
  function bollingerOf(v, n = 20, mult = 2) {
    const mid = smaOf(v, n), up = mid.slice(), low = mid.slice();
    for (let i = n - 1; i < v.length; i++) {
      const sd = Math.sqrt(v.slice(i - n + 1, i + 1).reduce((a, x) => a + (x - mid[i]) ** 2, 0) / n);
      up[i] = mid[i] + mult * sd;
      low[i] = mid[i] - mult * sd;
    }
    return { mid, up, low };
  }
  function rsiOf(v, n = 14) { // Wilder's smoothing, as TradingView's RSI
    const out = new Array(v.length).fill(null);
    let gain = 0, loss = 0;
    for (let i = 1; i < v.length; i++) {
      const d = v[i] - v[i - 1], g = Math.max(d, 0), l = Math.max(-d, 0);
      if (i <= n) {
        gain += g / n;
        loss += l / n;
      } else {
        gain = (gain * (n - 1) + g) / n;
        loss = (loss * (n - 1) + l) / n;
      }
      if (i >= n) out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
    }
    return out;
  }
  function macdOf(v, fast = 12, slow = 26, sig = 9) {
    const f = emaOf(v, fast), s = emaOf(v, slow);
    const macd = v.map((_, i) => (f[i] != null && s[i] != null ? f[i] - s[i] : null));
    const signal = emaOf(macd, sig);
    return { macd, signal, hist: macd.map((m, i) => (m != null && signal[i] != null ? m - signal[i] : null)) };
  }

  const IND_DEFAULTS = { ema20: false, sma50: true, sma200: true, bb: false, volume: true, usd: true, rsi: true, macd: false };
  const indBoxes = [...document.querySelectorAll("[data-ind]")];
  try {
    const saved = JSON.parse(localStorage.getItem("indicators") || "null");
    indBoxes.forEach((b) => { b.checked = saved && b.dataset.ind in saved ? saved[b.dataset.ind] : IND_DEFAULTS[b.dataset.ind]; });
  } catch (_) {}
  const indOn = (k) => indBoxes.find((b) => b.dataset.ind === k).checked;
  const usdBox = indBoxes.find((b) => b.dataset.ind === "usd");
  const usdShown = () => !!(state.data && state.data.usd && indOn("usd"));

  const line = (opts) => chart.addLineSeries({ lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...opts });
  const noScale = { autoscaleInfoProvider: () => null }; // bands shouldn't squash the candles
  const ind = {
    ema20: line({ title: "" }), sma50: line({ title: "" }),
    bbUp: line({ lineWidth: 1, ...noScale }), bbMid: line({ lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, ...noScale }),
    bbLow: line({ lineWidth: 1, ...noScale }),
    values: null, // name -> array aligned with the candles, for the legends
  };

  // RSI and MACD each get a chart of their own under the price chart, kept in step with it.
  function makePane(el) {
    return LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { attributionLogo: false }, // the price chart above carries the TradingView attribution
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      leftPriceScale: { borderVisible: false, visible: false }, // an empty one when the price chart shows USD, so bars line up
      timeScale: { borderVisible: false, rightOffset: 8, visible: false },
    });
  }
  const rsiPane = { el: $("rsiPane"), key: "rsi", legend: $("rsiLegend") };
  rsiPane.chart = makePane(rsiPane.el);
  rsiPane.main = rsiPane.chart.addLineSeries({ lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true,
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
  const macdPane = { el: $("macdPane"), key: "macd", legend: $("macdLegend") };
  macdPane.chart = makePane(macdPane.el);
  macdPane.hist = macdPane.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
  macdPane.main = macdPane.chart.addLineSeries({ lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true });
  macdPane.signal = macdPane.chart.addLineSeries({ lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  const panes = [rsiPane, macdPane];
  const shownPanes = () => panes.filter((p) => !p.el.hidden);

  function computeIndicators() {
    const cs = state.data.candles, warm = state.data.warmupCloses || [];
    const all = warm.concat(cs.map((c) => c.close)), cut = (a) => a.slice(warm.length);
    const bb = bollingerOf(all), m = macdOf(all);
    ind.values = { ema20: cut(emaOf(all, 20)), sma50: cut(smaOf(all, 50)), bbUp: cut(bb.up), bbMid: cut(bb.mid), bbLow: cut(bb.low),
                   rsi: cut(rsiOf(all)), macd: cut(m.macd), signal: cut(m.signal), hist: cut(m.hist) };
    // a time for every bar (blank where undefined) keeps each pane's bars lined up with the candles
    const data = (k) => cs.map((c, i) => (ind.values[k][i] == null ? { time: c.time } : { time: c.time, value: ind.values[k][i] }));
    for (const k of ["ema20", "sma50", "bbUp", "bbMid", "bbLow"]) ind[k].setData(data(k));
    rsiPane.main.setData(data("rsi"));
    macdPane.main.setData(data("macd"));
    macdPane.signal.setData(data("signal"));
    const up = css("--bull") + "99", down = css("--bear") + "99";
    macdPane.hist.setData(cs.map((c, i) => {
      const h = ind.values.hist[i];
      return h == null ? { time: c.time } : { time: c.time, value: h, color: h >= 0 ? up : down };
    }));
  }

  function applyIndicators() {
    ind.ema20.applyOptions({ visible: indOn("ema20") });
    ind.sma50.applyOptions({ visible: indOn("sma50") });
    for (const k of ["bbUp", "bbMid", "bbLow"]) ind[k].applyOptions({ visible: indOn("bb") });
    sma.applyOptions({ visible: indOn("sma200") });
    volume.applyOptions({ visible: indOn("volume") });
    usdBox.closest("label").hidden = !state.data.usd;
    usd.applyOptions({ visible: usdShown() });
    allCharts().forEach((c) => c.applyOptions({ leftPriceScale: { visible: usdShown() } }));
    for (const p of panes) p.el.hidden = !state.data || !indOn(p.key);
    // one time axis, under the lowest chart
    const shown = shownPanes();
    chart.timeScale().applyOptions({ visible: !shown.length });
    panes.forEach((p) => p.chart.timeScale().applyOptions({ visible: p === shown[shown.length - 1] }));
    const range = chart.timeScale().getVisibleLogicalRange();
    if (range) setRange(shown.map((p) => p.chart), range);
    $("indCount").textContent = `(${indBoxes.filter((b) => b.checked && !b.closest("label").hidden).length})`;
    alignScales();
    updatePaneLegends(null);
    const cs = state.data.candles;
    updateLegend(cs[cs.length - 1], cs[cs.length - 2], cs.length - 1);
  }

  // The panes' price axes (and the USD axis on the left) take the width of the widest, so every chart's bars line up.
  let alignTimer = null;
  function alignScales() {
    clearTimeout(alignTimer);
    const charts = [chart, ...shownPanes().map((p) => p.chart)];
    const sides = usdShown() ? ["right", "left"] : ["right"];
    charts.forEach((c) => c.applyOptions({ rightPriceScale: { minimumWidth: 0 }, leftPriceScale: { minimumWidth: 0 } }));
    alignTimer = setTimeout(() => {
      for (const side of sides) {
        const w = Math.max(...charts.map((c) => c.priceScale(side).width()));
        charts.forEach((c) => c.applyOptions({ [`${side}PriceScale`]: { minimumWidth: w } }));
      }
    }, 60);
  }

  function updatePaneLegends(i) {
    if (!ind.values) return;
    const v = ind.values, n = state.data.candles.length, at = i == null ? n - 1 : i;
    const f = (x, d = 2) => (x == null ? "—" : x.toFixed(d));
    const r = v.rsi[at];
    rsiPane.legend.innerHTML = `RSI 14 <b style="color:${css("--rsi")}">${f(r)}</b>${r == null ? "" : r >= 70 ? " · overbought" : r <= 30 ? " · oversold" : ""}`;
    const h = v.hist[at];
    macdPane.legend.innerHTML = `MACD 12 26 9 <b style="color:${css("--macd")}">${f(v.macd[at], 3)}</b> `
      + `<b style="color:${css("--signal")}">${f(v.signal[at], 3)}</b> <b style="color:${css(h >= 0 ? "--bull" : "--bear")}">${f(h, 3)}</b>`;
  }

  // Scrolling or zooming any chart moves the others; the crosshair follows across them too. The price chart
  // leads: a pane only moves the rest while the pointer is on it, since a pane being shown or resized
  // shifts its own view and would otherwise drag the price chart along.
  let syncing = false, hovered = null;
  const allCharts = () => [chart, ...panes.map((p) => p.chart)];
  const setRange = (targets, range) => {
    syncing = true;
    targets.forEach((o) => o.timeScale().setVisibleLogicalRange(range));
    syncing = false;
  };
  for (const p of panes) {
    p.el.addEventListener("pointerenter", () => { hovered = p.chart; });
    p.el.addEventListener("pointerleave", () => { if (hovered === p.chart) hovered = null; });
    new ResizeObserver(() => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!p.el.hidden && range) setTimeout(() => setRange([p.chart], range), 0);
    }).observe(p.el);
  }
  for (const c of allCharts()) {
    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range || (c !== chart && hovered !== c)) return;
      setRange(allCharts().filter((o) => o !== c), range);
    });
  }
  // i: the bar under the pointer (all charts share bar positions), or -1 when the pointer has left.
  function crosshairTo(i, from) {
    const cs = state.data.candles, time = i < 0 ? null : cs[i].time;
    if (from !== chart) {
      if (i < 0) chart.clearCrosshairPosition();
      else chart.setCrosshairPosition(cs[i].close, time, candles);
    }
    for (const p of shownPanes()) {
      if (p.chart === from) continue;
      const val = i < 0 ? null : ind.values[p.key][i];
      if (val == null) p.chart.clearCrosshairPosition();
      else p.chart.setCrosshairPosition(val, time, p.main);
    }
    updatePaneLegends(i < 0 ? null : i);
  }
  for (const c of allCharts()) {
    c.subscribeCrosshairMove((param) => {
      if (!state.data || !ind.values || !param.sourceEvent) return; // only real mouse moves, not the ones set here
      const n = state.data.candles.length;
      crosshairTo(param.point && param.logical != null ? Math.max(0, Math.min(n - 1, Math.round(param.logical))) : -1, c);
    });
  }
  [els.chart, ...panes.map((p) => p.el)].forEach((el) => el.addEventListener("mouseleave", () => state.data && ind.values && crosshairTo(-1, null)));

  indBoxes.forEach((b) => b.addEventListener("change", () => {
    try { localStorage.setItem("indicators", JSON.stringify(Object.fromEntries(indBoxes.map((x) => [x.dataset.ind, x.checked])))); } catch (_) {}
    if (state.data) applyIndicators();
  }));
  document.addEventListener("click", (e) => { // close the menu on a click elsewhere
    const menu = $("indMenu");
    if (menu.open && !menu.contains(e.target)) menu.open = false;
  });

  const logScale = $("logScale");
  try { const v = localStorage.getItem("logScale"); if (v != null) logScale.checked = v === "1"; } catch (_) {}
  function applyScaleMode() {
    const { Logarithmic, Normal } = LightweightCharts.PriceScaleMode;
    for (const side of ["right", "left"]) chart.priceScale(side).applyOptions({ mode: logScale.checked ? Logarithmic : Normal });
  }
  logScale.addEventListener("change", () => {
    applyScaleMode();
    try { localStorage.setItem("logScale", logScale.checked ? "1" : "0"); } catch (_) {}
  });
  applyScaleMode();

  let rsiBounds = [];
  function applyTheme() {
    const text = css("--text"), grid = css("--grid");
    const look = {
      layout: { background: { type: "solid", color: css("--panel") }, textColor: css("--muted"), fontFamily: css("--font") },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      crosshair: { vertLine: { labelBackgroundColor: text }, horzLine: { labelBackgroundColor: text } },
    };
    allCharts().forEach((c) => c.applyOptions(look));
    ind.ema20.applyOptions({ color: css("--ema20") });
    ind.sma50.applyOptions({ color: css("--sma50") });
    const bb = css("--bb");
    [ind.bbUp, ind.bbLow].forEach((s) => s.applyOptions({ color: bb + "aa" }));
    ind.bbMid.applyOptions({ color: bb + "77" });
    rsiPane.main.applyOptions({ color: css("--rsi") });
    rsiBounds.forEach((l) => rsiPane.main.removePriceLine(l));
    rsiBounds = [70, 30].map((price) => rsiPane.main.createPriceLine({
      price, color: css("--muted") + "99", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: "",
    }));
    macdPane.main.applyOptions({ color: css("--macd") });
    macdPane.signal.applyOptions({ color: css("--signal") });
    if (state.data && ind.values) computeIndicators(); // the MACD bars take their colours when the data is set
    // Hollow up candles, filled down candles, one ink color (TradingView "hollow" style).
    const ink = css("--candle");
    candles.applyOptions({
      upColor: css("--panel"), downColor: ink,
      borderUpColor: ink, borderDownColor: ink,
      wickUpColor: ink, wickDownColor: ink,
    });
    sma.applyOptions({ color: css("--sma") });
    usd.applyOptions({ color: css("--usd") });
    if (state.data) render();
  }

  // ---------- helpers ----------
  const catEnabled = (cat) => document.querySelector(`[data-cat="${cat}"]`).checked;
  const minConf = () => Number(els.minConf.value) / 100;
  const dirColor = (d) => css(d === "bullish" ? "--bull" : d === "bearish" ? "--bear" : "--neutral");
  const decimals = (v) => (Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 5);
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: decimals(v), maximumFractionDigits: decimals(v) }));
  const fmtVol = (v) => Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v);
  const timeLabel = (t) => (typeof t === "string" ? t : new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "));
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Patterns the layer checkboxes and confidence slider let through.
  function filteredPatterns() {
    if (!state.data) return [];
    return state.data.patterns.filter((p) => catEnabled(p.category) && p.confidence >= minConf());
  }

  const RECENT_BARS = 60; // "Recent": patterns that ended within this many bars

  // The Show setting: what drives the outlook (key), the last few months (recent), or everything.
  function shown(p) {
    const mode = els.showMode.value, o = state.data.outlook;
    if (mode === "all" || state.selected === p.id) return true;
    if (mode === "key") return !!o && (o.mainId === p.id || o.patternIds.includes(p.id));
    const age = state.data.candles.length - 1 - p.end;
    return age <= RECENT_BARS;
  }

  function visiblePatterns() {
    return filteredPatterns().filter(shown);
  }

  function setStatus(msg, isError = false) {
    els.status.hidden = !msg;
    els.status.innerHTML = msg || "";
    els.status.classList.toggle("error", isError);
  }

  // Line series need strictly ascending, unique times.
  function cleanLine(points) {
    const out = [];
    for (const p of [...points].sort((a, b) => a.index - b.index)) {
      if (out.length && out[out.length - 1].index === p.index) continue;
      out.push(p);
    }
    return out.map((p) => ({ time: p.time, value: p.value }));
  }

  function addLine(points, color, width, style, extra = {}) {
    const data = cleanLine(points);
    if (data.length < 2) return;
    const s = chart.addLineSeries({
      color, lineWidth: width, lineStyle: style,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null, // overlays should not stretch the price scale
      ...extra,
    });
    s.setData(data);
    state.overlays.push(s);
  }

  const NECK_LIVE_BARS = 30; // necklines of patterns this recent are extended to the last bar

  // A pattern's neckline (or cup rim). While it is still relevant it runs to the latest bar
  // with its price labelled on the axis, like a hand-drawn TradingView line.
  function addNeckline(p, color, width, live) {
    let pts = p.neckline;
    const cs = state.data.candles, lastIdx = cs.length - 1;
    const [a, b] = [pts[0], pts[pts.length - 1]];
    if (live && b.index < lastIdx) {
      const slope = b.index !== a.index ? (b.value - a.value) / (b.index - a.index) : 0;
      const v = b.value + slope * (lastIdx - b.index);
      if (v > 0) pts = [...pts, { index: lastIdx, time: cs[lastIdx].time, value: v }];
    }
    addLine(pts, color, width, LightweightCharts.LineStyle.Solid, live
      ? { lastValueVisible: true, title: /Cup|Rounding/.test(p.name) ? "Rim" : "Neckline" }
      : {});
  }

  // ---------- rendering ----------
  function clearOverlays() {
    state.overlays.forEach((s) => chart.removeSeries(s));
    state.overlays = [];
    state.priceLines.forEach((l) => candles.removePriceLine(l));
    state.priceLines = [];
  }

  function render() {
    clearOverlays();
    const pats = visiblePatterns();
    const { Solid, Dashed, Dotted } = LightweightCharts.LineStyle;
    const markers = [];

    for (const p of pats) {
      const color = dirColor(p.direction);
      const sel = state.selected === p.id;
      const dim = state.selected != null && !sel;
      const w = sel ? 3 : dim ? 1 : 2;
      const c = dim ? color + "55" : color;
      p.lines.forEach((ln, i) => {
        const isTarget = p.target != null && i === p.lines.length - 1;
        // A gap's two lines mark the empty zone it left, so they're dashed rather than drawn like a pattern's edges.
        addLine(ln, c, isTarget ? 1 : w, isTarget ? Dotted : p.category === "gap" ? Dashed : Solid);
      });
      if (p.neckline) {
        const live = sel || (state.selected == null && p.end >= state.data.candles.length - 1 - NECK_LIVE_BARS);
        addNeckline(p, c, w, live);
      }
      if (!dim) {
        const anchor = p.points.reduce((a, b) => (p.direction === "bearish" ? (b.value > a.value ? b : a) : (b.value < a.value ? b : a)), p.points[0]);
        markers.push({
          time: anchor.time, position: p.direction === "bearish" ? "aboveBar" : "belowBar",
          shape: "square", color, text: p.label, size: 0.1,
        });
      }
    }

    // Active trade plan: target and stop-loss lines.
    const plan = state.data.outlook && state.data.outlook.plans.find((p) => !p.conditional);
    if (plan) {
      state.priceLines.push(candles.createPriceLine({ price: plan.target, color: css("--bull"), lineWidth: 2, lineStyle: Dashed, axisLabelVisible: true, title: "Target" }));
      state.priceLines.push(candles.createPriceLine({ price: plan.stop, color: css("--bear"), lineWidth: 2, lineStyle: Dashed, axisLabelVisible: true, title: "Stop" }));
    }

    if (catEnabled("levels")) {
      for (const lv of state.data.levels) {
        state.priceLines.push(candles.createPriceLine({
          price: lv.price, color: css(lv.kind === "support" ? "--support" : "--resistance"),
          lineWidth: 1, lineStyle: Dashed, axisLabelVisible: true, title: `${lv.kind === "support" ? "S" : "R"} ×${lv.touches}`,
        }));
      }
    }

    for (const a of alertUi.list.filter((x) => !x.triggeredAt)) {
      state.priceLines.push(candles.createPriceLine({
        price: a.price, color: css("--accent"), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.LargeDashed,
        axisLabelVisible: true, title: `🔔${a.note ? ` ${a.note.slice(0, 18)}` : ""}`,
      }));
    }

    const order = new Map(state.data.candles.map((c, i) => [c.time, i]));
    markers.sort((a, b) => order.get(a.time) - order.get(b.time));
    candles.setMarkers(markers);
    renderList(pats);
  }

  const TRACK_LABELS = { useful: "Useful", misleading: "Misleading", unclear: "Unclear", none: "No edge", few: "Not enough data" };

  // How this pattern has done across the market's Track record run (none for trendlines or patterns pointing neither way).
  function trackLine(p) {
    const t = state.data.patternTrack;
    if (!t || p.category === "trend" || p.direction === "neutral") return "";
    const s = t.stats[`${p.name}|${p.direction}`];
    if (!s) return `<div class="meta track-line">No ${escapeHtml(t.label)} track record for it yet</div>`;
    const tip = `Track record: ${s.n} completed on ${t.symbols} ${t.label} symbols since ${t.since}, price change over the ${t.horizon}`;
    const said = `${p.direction === "bullish" ? "rose" : "fell"} after ${Math.round(s.asExpected)}% of ${s.n.toLocaleString()} vs ${Math.round(s.baseRate)}% of any period`;
    return `<div class="meta track-line" title="${escapeHtml(tip)}"><span class="verdict-pill ${s.verdict}">${TRACK_LABELS[s.verdict]}</span> ${escapeHtml(t.label)}: ${said}</div>`
      + successLine(s, t);
  }

  // Success rate: how often this pattern's target was reached before its stop, on the market's track record.
  function successLine(s, t) {
    if (!s.trades) return "";
    const tip = `Out of ${s.trades} completed ones on ${t.symbols} ${t.label} symbols: target first ${Math.round(s.targetFirst)}%, `
      + `stop first ${Math.round(s.stopFirst)}%, neither within ${t.tradeHorizon} ${Math.max(0, Math.round(100 - s.targetFirst - s.stopFirst))}%`;
    return `<div class="meta success-line" title="${escapeHtml(tip)}"><span class="success-rate ${successTone(s.targetFirst)}">${Math.round(s.targetFirst)}% success</span>
      reached its target before its stop in ${s.trades.toLocaleString()} past trades · stopped out ${Math.round(s.stopFirst)}%</div>`;
  }
  const successTone = (r) => (r >= 50 ? "good" : r >= 35 ? "mid" : "bad");

  // The period asked for. A short one comes with extra history (the outlook always reads 250 bars), so the
  // chart opens on just the asked-for bars and the rest is a scroll away.
  function defaultView() {
    const n = state.data.candles.length, v = state.data.viewBars;
    if (v && v < n) chart.timeScale().setVisibleLogicalRange({ from: n - v - 0.5, to: n - 0.5 + 8 });
    else chart.timeScale().fitContent();
  }

  // The outlook's main signal above the chart: what it is, where it points, and how that pattern has done.
  function renderMain() {
    const o = state.data.outlook, t = state.data.patternTrack, box = $("mainPattern");
    const p = o && o.mainId != null ? state.data.patterns.find((x) => x.id === o.mainId) : null;
    box.hidden = !p;
    if (!p) return;
    // The plan follows the overall bias, which can disagree with the main pattern; its R:R only belongs here when they agree.
    const plan = o.plans.find((x) => !x.conditional && (x.side === "long") === (p.direction === "bullish"));
    const s = t && t.stats[`${p.name}|${p.direction}`];
    const status = p.confirmed ? "confirmed" : p.category === "trend" ? "active" : "forming";
    // the strongest single pattern can be outweighed by the trend and the rest: say so rather than look like the verdict
    const overall = /bullish/i.test(o.bias) ? "bullish" : /bearish/i.test(o.bias) ? "bearish" : "neutral";
    const outweighed = overall !== p.direction
      ? `<span class="muted">· outweighed: overall the chart says <b class="tag ${overall}">${escapeHtml(o.bias)}</b></span>` : "";
    box.dataset.id = p.id;
    box.innerHTML = `<span class="mp-k">Main pattern</span> <b>${escapeHtml(p.name)}</b> <span class="tag ${p.direction}">${p.direction}</span>
      <span class="muted">${status}${p.target != null ? ` · target ≈ ${fmt(p.target)}` : ""}${plan ? ` · R:R ${plan.rr.toFixed(2)}` : ""}</span>
      ${s && s.verdict ? `<span class="verdict-pill ${s.verdict}" title="${escapeHtml(`${t.label} track record: ${p.direction === "bullish" ? "rose" : "fell"} after ${Math.round(s.asExpected)}% of ${s.n} vs ${Math.round(s.baseRate)}% of any period`)}">${TRACK_LABELS[s.verdict]} on ${escapeHtml(t.label)}</span>` : ""}
      ${s && s.trades ? `<span class="success-rate ${successTone(s.targetFirst)}" title="Reached its target before its stop in ${Math.round(s.targetFirst)}% of ${s.trades} past ${escapeHtml(t.label)} trades">${Math.round(s.targetFirst)}% success</span>` : ""}
      ${outweighed}`;
  }

  // A PNG of the chart (with any measurement on it) and the symbol, interval and date written above it.
  async function snapshot() {
    const shot = chart.takeScreenshot();
    if (layer.childNodes.length) {
      const svg = layer.cloneNode(true);
      svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      svg.setAttribute("width", els.chart.clientWidth);
      svg.setAttribute("height", els.chart.clientHeight);
      const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" }));
      const img = new Image();
      img.src = url;
      await img.decode();
      shot.getContext("2d").drawImage(img, 0, 0, shot.width, shot.height);
      URL.revokeObjectURL(url);
    }
    const ratio = shot.width / els.chart.clientWidth, head = Math.round(34 * ratio);
    // the RSI / MACD panes go under the price chart, each with its legend written on it
    const paneShots = shownPanes().map((p) => {
      const s = p.chart.takeScreenshot(), g = s.getContext("2d");
      g.font = `${Math.round(12 * ratio)}px ${css("--font")}`;
      g.fillStyle = css("--muted");
      g.textBaseline = "top";
      g.fillText(p.legend.textContent, 8 * ratio, 5 * ratio);
      return s;
    });
    const out = document.createElement("canvas");
    out.width = shot.width;
    out.height = head + shot.height + paneShots.reduce((h, s) => h + s.height, 0);
    const g = out.getContext("2d");
    g.fillStyle = css("--panel");
    g.fillRect(0, 0, out.width, out.height);
    g.drawImage(shot, 0, head);
    let y = head + shot.height;
    for (const s of paneShots) {
      g.drawImage(s, 0, y);
      y += s.height;
    }
    const cs = state.data.candles, day = timeLabel(cs[cs.length - 1].time).slice(0, 10);
    const iv = els.interval.selectedOptions[0].textContent;
    g.fillStyle = css("--text");
    g.font = `600 ${Math.round(15 * ratio)}px ${css("--font")}`;
    g.textBaseline = "middle";
    g.fillText(`${state.data.name} (${state.data.symbol}) · ${iv} · ${day}`, 10 * ratio, head / 2);
    out.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${state.data.symbol.replace(/[^\w.-]+/g, "_")}_${els.interval.value}_${day}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
  }

  function renderList(pats) {
    const chartPats = pats.slice().reverse();
    els.count.textContent = chartPats.length;
    const item = (p) => `
      <li data-id="${p.id}" class="${p.direction}${state.selected === p.id ? " selected" : ""}">
        <div class="row"><span class="name">${escapeHtml(p.name)}</span><span class="tag ${p.direction}">${p.direction}</span></div>
        <div class="meta">${timeLabel(p.startTime)} → ${timeLabel(p.endTime)} · ${p.category}${p.confirmed ? " · confirmed" : ["reversal", "continuation", "gap"].includes(p.category) ? " · forming" : ""}</div>
        ${p.target != null ? `<div class="meta">Target ≈ ${fmt(p.target)}</div>` : ""}
        ${trackLine(p)}
        <div class="confbar" title="Confidence ${Math.round(p.confidence * 100)}%"><span style="width:${p.confidence * 100}%"></span></div>
      </li>`;
    const hidden = filteredPatterns().length - pats.length;
    const showAll = `<button type="button" class="link-inline" data-show="all">Show all</button>`;
    const more = !hidden ? "" : els.showMode.value === "key"
      ? `<li class="empty more">Showing only what drives the outlook; ${hidden} older or weaker pattern${hidden === 1 ? "" : "s"} hidden. ${showAll}</li>`
      : `<li class="empty more">${hidden} older pattern${hidden === 1 ? "" : "s"} hidden. ${showAll}</li>`;
    els.list.innerHTML = chartPats.length
      ? chartPats.map(item).join("") + more
      : els.showMode.value === "key" && hidden
        ? `<li class="empty more">No pattern is driving the outlook right now; the trend and levels do. ${showAll}</li>`
        : `<li class="empty">No patterns match the current filters.</li>`;

    const last = state.data.candles[state.data.candles.length - 1].close;
    els.levels.innerHTML = state.data.levels.length
      ? [...state.data.levels].reverse().map((lv) => `
        <li><span class="${lv.kind}">${lv.kind === "support" ? "Support" : "Resistance"} · ${lv.touches} touches</span>
        <span>${fmt(lv.price)} <span class="muted">(${(((lv.price - last) / last) * 100).toFixed(1)}%)</span></span></li>`).join("")
      : `<li class="empty">No clear levels.</li>`;
  }

  function renderOutlook(o) {
    const box = $("outlook");
    box.hidden = !o;
    $("dsChart").parentElement.hidden = !o;
    $("decisionSummary").hidden = !o;
    if (!o) return;
    const b = $("outlookBias");
    b.textContent = o.bias;
    b.className = "bias " + (/bullish/i.test(o.bias) ? "bullish" : /bearish/i.test(o.bias) ? "bearish" : "");
    $("dsChart").textContent = o.bias;
    $("dsChart").className = b.className;
    $("outlookHorizon").textContent = `Expectation for the ${o.horizon}`;
    $("outlookSummary").textContent = o.summary;
    $("outlookBull").textContent = o.bullCase;
    $("outlookBear").textContent = o.bearCase;
    $("outlookRange").textContent = o.range.text;
    const pct = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
    $("outlookPlans").innerHTML = o.plans.map((p) => `
      <div class="plan">
        <div class="plan-title"><span>${escapeHtml(p.label)}</span><span class="rr">R:R ${p.rr.toFixed(2)}</span></div>
        <div class="plan-grid">
          <span class="k">${p.conditional ? "Entry (trigger)" : "Entry (last close)"}</span><span class="v">${fmt(p.entry)}</span><span></span>
          <span class="k">Target</span><span class="v tgt">${fmt(p.target)}</span><span class="pct">${pct(p.targetPct)}</span>
          <span class="k">Stop loss</span><span class="v stp">${fmt(p.stop)}</span><span class="pct">${pct(p.stopPct)}</span>
        </div>
        <div class="basis">Stop: ${escapeHtml(p.stopBasis)}</div>
        ${p.warning ? `<div class="warn">⚠ ${escapeHtml(p.warning)}</div>` : ""}
      </div>`).join("");
    renderDecision();
    renderSize(o);
    $("outlookDrivers").innerHTML = o.drivers.length
      ? o.drivers.map((d) => `<li class="${d.direction}">${escapeHtml(d.text)}</li>`).join("")
      : `<li>No strong signals.</li>`;
  }

  // ---------- layers toggle (phones fold the filter row away) ----------
  const filtersBox = $("filters");
  function layersSummary() {
    const on = filtersBox.querySelectorAll("input[data-cat]:checked").length;
    const all = filtersBox.querySelectorAll("input[data-cat]").length;
    $("layersCount").textContent = `${on} of ${all} on · ${els.minConf.value}%+`;
  }
  $("layersToggle").addEventListener("click", () => {
    const open = filtersBox.classList.toggle("open");
    $("layersToggle").setAttribute("aria-expanded", String(open));
  });
  filtersBox.addEventListener("input", layersSummary);
  layersSummary();

  // ---------- sidebar: summary strip and tabs ----------
  const TABS = ["decision", "outlook", "patterns", "levels"];
  let preferredTab = "decision";
  try { const t = localStorage.getItem("sideTab"); if (TABS.includes(t)) preferredTab = t; } catch (_) {}
  let currentTab = null;
  function showTab(name, remember = true) {
    currentTab = name;
    for (const t of TABS) {
      $(`tab-${t}`).setAttribute("aria-selected", String(t === name));
      $(`tab-${t}`).tabIndex = t === name ? 0 : -1;
      $(`panel-${t}`).hidden = t !== name;
    }
    if (remember) {
      preferredTab = name;
      try { localStorage.setItem("sideTab", name); } catch (_) {}
    }
  }
  showTab(preferredTab, false);
  document.querySelector(".side-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  document.querySelector(".side-tabs").addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    const next = TABS[(TABS.indexOf(currentTab) + step + TABS.length) % TABS.length];
    showTab(next);
    $(`tab-${next}`).focus();
  });
  $("decisionSummary").addEventListener("click", (e) => {
    const b = e.target.closest("[data-go]");
    if (!b) return;
    showTab(b.dataset.go);
    // Scroll the sidebar itself on desktop (it scrolls on its own); on phones it doesn't, so scroll the page.
    const side = document.querySelector(".sidebar"), target = $(b.dataset.target);
    const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    if (side.scrollHeight > side.clientHeight + 1 && getComputedStyle(side).overflowY !== "visible") {
      const tabs = document.querySelector(".side-tabs").offsetHeight;
      side.scrollTo({ top: side.scrollTop + target.getBoundingClientRect().top - side.getBoundingClientRect().top - tabs - 8, behavior });
    } else {
      target.scrollIntoView({ block: "start", behavior });
    }
  });

  // The summary strip's checklist and rating pills; with no checklist (indices, intraday) the Decision tab steps aside.
  function setDecision(c) {
    const has = !!(c && c.applicable);
    $("decisionEmpty").hidden = has;
    $("dsVerdict").parentElement.hidden = !has;
    $("dsRating").parentElement.hidden = !(has && c.rating);
    if (has) {
      $("dsVerdict").textContent = c.label;
      $("dsVerdict").className = `verdict ${c.verdict}`;
      if (c.rating) {
        $("dsRating").textContent = c.rating.signal.label;
        $("dsRating").className = `rsig ${c.rating.signal.key}`;
      }
      if (currentTab !== preferredTab) showTab(preferredTab, false);
    } else if (currentTab === "decision") {
      showTab("outlook", false);
    }
  }

  // ---------- buy checklist (/api/decision) ----------
  const CHECK_ICON = { pass: "✓", warn: "!", fail: "✗", unknown: "?" };
  async function renderChecklist() {
    const box = $("checklist"), d = state.data;
    box.hidden = true;
    $("rating").hidden = true;
    $("decisionEmpty").hidden = true;
    if (!d.currency || !["1d", "1wk"].includes(d.interval)) return setDecision(null); // indices and intraday bars: no checklist
    let c;
    try {
      const r = await fetch(`/api/decision?${new URLSearchParams({ symbol: d.symbol, interval: d.interval })}`);
      if (!r.ok) return state.data === d && setDecision(null);
      c = await r.json();
    } catch (_) { return state.data === d && setDecision(null); }
    if (state.data !== d) return; // another symbol loaded meanwhile
    if (!c.applicable) return setDecision(null);
    setDecision(c);
    const v = $("checklistVerdict");
    v.textContent = c.label;
    v.className = `verdict ${c.verdict}`;
    $("checklistReason").innerHTML = `${escapeHtml(c.reason)} <span class="muted small">${c.passed} of ${c.known} checks with data pass.</span>`
      + (c.position && c.position !== "none" ? ' <span class="muted small">You hold it: see My profile for what to do with it.</span>' : "");
    $("checklistItems").innerHTML = c.checks.map((k) => `
      <li class="${k.status}"><span class="ic">${CHECK_ICON[k.status]}</span><span class="lbl">${escapeHtml(k.label)}</span>
        <span class="det">${escapeHtml(k.detail)}</span></li>`).join("");
    box.hidden = false;
    renderRating(c.rating);
  }

  // ---------- fundamental + technical rating (part of /api/decision) ----------
  const SIGNED = new Set(["aboveLong", "aboveShort", "slope", "momentum", "nearHigh"]);
  const level = (p, good, ok) => (p == null ? "" : p >= good ? "good" : p >= ok ? "warn" : "bad");
  function ratingValue(i) {
    if (i.value == null) return "—";
    if (i.unit === "%") return `${SIGNED.has(i.key) && i.value > 0 ? "+" : ""}${i.value.toFixed(1)}%`;
    if (i.unit === "×") return `${i.value.toFixed(2)}×`;
    return i.value.toFixed(0);
  }
  const ratingItems = (items) => items.map((i) => `
    <li class="${i.status}"><span class="ri-dot"></span><span class="ri-lbl">${escapeHtml(i.label)}</span><span class="ri-val">${ratingValue(i)}</span>
      <span class="ri-det">${escapeHtml([i.note, i.rule].filter(Boolean).join(" · "))}</span></li>`).join("");
  function setScore(prefix, pct, label, lv) {
    $(`rating${prefix}Pct`).textContent = pct == null ? "n/a" : `${pct}%`;
    const bar = $(`rating${prefix}Bar`);
    bar.style.width = `${pct || 0}%`;
    bar.className = lv;
    $(`rating${prefix}Label`).textContent = label;
  }
  function renderRating(r) {
    const box = $("rating");
    if (!r) return;
    const f = r.fundamental, t = r.technical || { items: [], pct: null, label: "Not enough data" }, s = r.signal;
    const sig = $("ratingSignal");
    sig.textContent = s.label;
    sig.className = `rsig ${s.key}`;
    $("ratingText").innerHTML = `${escapeHtml(s.text)}${s.score != null ? ` <span class="muted small">Combined score ${s.score}%.</span>` : ""}`;
    setScore("Fund", f.pct, `${f.label} · ${f.scored} figures scored${f.financial ? " · bank / lender rules" : ""}`, level(f.pct, 75, 50));
    setScore("Tech", t.pct, `${t.label} · ${t.scored || 0} checks scored`, level(t.pct, 65, 36));
    $("ratingNotes").innerHTML = s.notes.filter((n) => !/red flag/.test(n)).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
    $("ratingFlags").innerHTML = f.flags.map((x) => `<li><b>${escapeHtml(x.title)}</b> ${escapeHtml(x.text)}</li>`).join("");
    $("ratingFundDetail").innerHTML = f.categories.map((c) => `
      <div class="rcat">
        <div class="rcat-head"><span>${escapeHtml(c.label)}</span><span class="muted">${c.max ? `${c.points}/${c.max}` : "not scored"}</span></div>
        <div class="rbar"><i class="${level(c.pct, 75, 50)}" style="width:${c.pct || 0}%"></i></div>
        <ul class="rating-items">${ratingItems(c.items)}</ul>
      </div>`).join("");
    $("ratingTechDetail").innerHTML = t.items.length ? ratingItems(t.items) : '<li class="na">Only daily and weekly bars are scored.</li>';
    $("ratingSource").textContent = `Fundamentals: ${r.source || "none reported"}${f.inflationPct != null ? `, growth and returns after ${f.inflationPct}% inflation` : ""}. ${r.note}`;
    box.hidden = false;
  }

  // ---------- position size (rules saved in My profile) ----------
  async function renderSize(o) {
    const box = $("planSize");
    const buy = o.plans.find((p) => p.side === "long" && !p.conditional);
    const entry = state.data.candles[state.data.candles.length - 1].close;
    const stop = buy ? buy.stop : o.decision && o.decision.stop;
    const symbol = state.data.symbol;
    box.hidden = true;
    if (!stop) return;
    let z;
    try {
      const q = new URLSearchParams({ entry, stop, currency: state.data.currency || "" });
      if (state.data.adtv) q.set("adtv", state.data.adtv);
      z = await (await fetch(`/api/size?${q}`)).json();
    } catch (_) { return; }
    if (!state.data || state.data.symbol !== symbol) return; // another symbol loaded meanwhile
    const cur = escapeHtml(z.currency || "");
    const pct = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
    box.innerHTML = z.ok
      ? `<h3>💰 ${buy ? "How much to buy" : "If you decide to buy"}</h3>
         Up to <b>${z.shares.toLocaleString()} shares</b> (about ${fmt(z.cost)} ${cur}, ${z.costPct}% of your capital),
         with a stop at <b class="stp">${fmt(z.stop)}</b> (${pct(z.stopPct)}). If it's hit you lose about
         <b class="down">${fmt(z.risk)} ${cur}</b> (${z.riskPct}% of your capital).
         ${z.limitedBy === "size" ? '<div class="muted small">Capped by your limit per stock.</div>' : ""}
         ${z.limitedBy === "liquidity" ? `<div class="muted small">💧 Capped by liquidity: it trades about ${fmt(state.data.adtv)} a day.</div>` : ""}
         ${z.feePct ? `<div class="muted small">Includes ${z.feePct}% costs on the buy and on the sell.</div>` : ""}
         <div class="muted small">Sizing only controls how much you can lose. It says nothing about whether the stock will rise.</div>`
      : `<h3>💰 ${buy ? "How much to buy" : "If you decide to buy"}</h3><span class="muted">${escapeHtml(z.reason)}</span>${z.configured === false ? ' <a href="/profile#settingsForm">Set it in My profile</a>' : ""}`;
    box.hidden = false;
  }

  // ---------- decision ----------
  const buyKey = () => `buyPrice:${state.data.symbol}`;
  const adviceHtml = (title, a) => `
    <div class="dec-title"><span>${title}</span><span class="action ${a.tone}">${escapeHtml(a.action)}</span></div>
    <div class="dec-text">${escapeHtml(a.text)}</div>`;

  function renderDecision() {
    const d = state.data && state.data.outlook && state.data.outlook.decision;
    if (!d) return;
    const last = state.data.candles[state.data.candles.length - 1].close;
    const buy = Number($("buyPrice").value);
    const pnl = buy > 0 ? ((last - buy) / buy) * 100 : null;
    const inProfit = pnl != null && pnl >= 0;

    $("holdPnl").innerHTML = pnl == null ? "Enter your buy price to see which case applies."
      : `Your position: <span class="${inProfit ? "up" : "down"}">${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}%</span> (last ${fmt(last)})`
        + (buy < d.stop ? ` · suggested stop ${fmt(d.stop)} is above your buy price, so it locks in a gain` : "");
    const pBox = $("holdProfit"), lBox = $("holdLoss");
    pBox.innerHTML = adviceHtml("In profit", d.holder.profit);
    lBox.innerHTML = adviceHtml("At a loss", d.holder.loss);
    pBox.classList.toggle("active", pnl != null && inProfit);
    lBox.classList.toggle("active", pnl != null && !inProfit);
    pBox.classList.toggle("dim", pnl != null && !inProfit);
    lBox.classList.toggle("dim", pnl != null && inProfit);
    $("newcomer").innerHTML = adviceHtml("No position", d.newcomer);
    $("longTerm").textContent = d.longTerm || "";
  }

  $("buyPrice").addEventListener("input", () => {
    if (!state.data) return;
    try { localStorage.setItem(buyKey(), $("buyPrice").value); } catch (_) {}
    renderDecision();
  });

  function selectPattern(id) {
    state.selected = state.selected === id ? null : id;
    render();
    if (state.selected == null) return;
    const p = state.data.patterns.find((x) => x.id === id);
    const pad = Math.max(10, Math.round((p.end - p.start) * 0.5));
    chart.timeScale().setVisibleLogicalRange({ from: p.start - pad, to: p.end + pad });
  }

  function updateLegend(c, prev, i) {
    if (!c) { els.legend.innerHTML = ""; return; }
    const chg = prev ? ((c.close - prev.close) / prev.close) * 100 : 0;
    const cls = chg >= 0 ? "up" : "down";
    const m = indOn("sma200") && state.smaByTime ? state.smaByTime.get(c.time) : null;
    const v = ind.values, val = (k) => (v && v[k][i] != null ? fmt(v[k][i]) : null);
    const u = usdShown() && state.usdByTime ? state.usdByTime.get(c.time) : null;
    const extra = [
      indOn("ema20") && val("ema20") && `<span class="ema20">EMA 20 ${val("ema20")}</span>`,
      indOn("sma50") && val("sma50") && `<span class="sma50">SMA 50 ${val("sma50")}</span>`,
      m != null && `<span class="sma">SMA ${state.data.sma.length} ${fmt(m)}</span>`,
      indOn("bb") && val("bbUp") && `<span class="bb">BB ${val("bbLow")} – ${val("bbUp")}</span>`,
      u && `<span class="usd" title="Close ÷ USD / EGP rate">$${fmt(u.value)} <span class="muted">at ${fmt(u.rate)} EGP/$</span></span>`,
    ].filter(Boolean);
    els.legend.innerHTML = `${timeLabel(c.time)} &nbsp; O ${fmt(c.open)} H ${fmt(c.high)} L ${fmt(c.low)} C <span class="${cls}">${fmt(c.close)} (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)</span>`
      + (indOn("volume") ? ` &nbsp; Vol ${fmtVol(c.volume)}` : "") + extra.map((s) => ` &nbsp; ${s}`).join("");
  }

  chart.subscribeCrosshairMove((param) => {
    if (!state.data) return;
    const cs = state.data.candles;
    const i = param.logical != null ? Math.max(0, Math.min(cs.length - 1, Math.round(param.logical))) : cs.length - 1;
    updateLegend(cs[i], cs[i - 1], i);
  });

  // ---------- data loading ----------
  // ---------- "Add to my profile" ----------
  // The profile keeps EGX stocks as XXX.CA (Yahoo style) even when the chart came from TradingView (EGX:XXX);
  // indices keep the name that was typed (EGX30).
  const profileSymbol = (d, typed) => (!d.currency ? typed : d.symbol.startsWith("EGX:") ? `${d.symbol.slice(4)}.CA` : d.symbol);
  const canon = (s) => String(s || "").toUpperCase().replace(/^EGX:(.+)$/, "$1.CA");

  // Halal tag (stocks only): from the EGX 33 Shariah list in My profile, or my own tag.
  const halalBadge = (h) => (!h ? "" : h.status === "halal"
    ? `<span class="halal${h.source === "mine" ? " mine" : ""}" title="${h.source === "mine" ? "Halal: your own tag" : `In the EGX 33 Shariah list${h.listDate ? ` (from ${h.listDate})` : ""}`}">Halal</span>`
    : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');

  function renderHalal() {
    const slot = $("halalSlot"), d = state.data;
    if (!d.currency) { slot.innerHTML = ""; return; }
    const mine = d.halal?.source === "mine" ? d.halal.status : "";
    slot.innerHTML = `${halalBadge(d.halal)}
      <select class="halal-select" title="Tag this stock halal (Shariah compliant) yourself, or follow the EGX 33 Shariah list saved in My profile">
        <option value="">Shariah: use list</option>
        <option value="halal" ${mine === "halal" ? "selected" : ""}>Mark halal</option>
        <option value="not" ${mine === "not" ? "selected" : ""}>Mark not halal</option>
      </select>`;
    slot.querySelector("select").addEventListener("change", async (ev) => {
      ev.target.disabled = true;
      try {
        const r = await fetch("/api/shariah/tag", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: d.symbol, status: ev.target.value || null }) });
        const b = await r.json();
        if (!r.ok) throw new Error(b.detail || `Request failed (${r.status})`);
        d.halal = b.halal;
        if (state.data === d) renderChecklist();
      } catch (err) {
        setStatus(escapeHtml(err.message), true);
      }
      if (state.data === d) renderHalal();
    });
  }

  async function renderProfileButton(typed) {
    const slot = $("profileSlot");
    const d = state.data;
    slot.innerHTML = "";
    let entries = [];
    try {
      const r = await fetch("/api/profile?live=false");
      if (r.ok) entries = (await r.json()).entries || [];
    } catch (_) { return; }
    if (state.data !== d) return; // another symbol loaded meanwhile
    const sym = profileSymbol(d, typed);
    const mine = new Set(entries.map((e) => canon(e.symbol)));
    const found = [sym, typed, d.symbol].map(canon).find((s) => mine.has(s));
    const link = (s) => `/profile#card=${encodeURIComponent(entries.find((e) => canon(e.symbol) === s)?.symbol || sym)}`;
    if (found) {
      slot.innerHTML = `<a class="profile-btn in" href="${link(found)}" title="Open its card in My profile">✓ In my profile</a>`;
      return;
    }
    slot.innerHTML = `<button type="button" class="profile-btn" title="Add ${escapeHtml(sym)} to My profile as a stock you watch">＋ Add to my profile</button>`;
    slot.querySelector("button").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        const r = await fetch("/api/profile/entries", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: sym, position: "none", expectation: "neutral" }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || `Request failed (${r.status})`);
        slot.innerHTML = `<a class="profile-btn in" href="/profile#card=${encodeURIComponent(sym)}" title="Open it to set your view, stop and target">✓ Added: open in My profile</a>`;
      } catch (err) {
        btn.disabled = false;
        slot.insertAdjacentHTML("beforeend", ` <span class="down small">${escapeHtml(err.message)}</span>`);
      }
    });
  }

  // ---------- "Open in TradingView" ----------
  // Turns the loaded (Yahoo-style) symbol into a TradingView code: COMI.CA → EGX:COMI, ^GSPC → SP:SPX, GC=F → COMEX:GC1!.
  const TV_INDICES = { "^GSPC": "SP:SPX", "^NDX": "NASDAQ:NDX", "^IXIC": "NASDAQ:IXIC", "^DJI": "DJ:DJI", "^RUT": "TVC:RUT", "^VIX": "TVC:VIX", "^FTSE": "TVC:UKX", "^GDAXI": "XETR:DAX", "^N225": "TVC:NI225" };
  const TV_FUTURES = { GC: "COMEX:GC1!", SI: "COMEX:SI1!", HG: "COMEX:HG1!", CL: "NYMEX:CL1!", NG: "NYMEX:NG1!", BZ: "NYMEX:BB1!" };
  const TV_SUFFIX = { CA: "EGX", L: "LSE", DE: "XETR", PA: "EURONEXT", AS: "EURONEXT", TO: "TSX", HK: "HKEX", T: "TSE", AX: "ASX", SR: "TADAWUL" };
  const TV_INTERVAL = { "1h": "60", "1d": "D", "1wk": "W", "1mo": "M" };
  function tradingViewSymbol(s) {
    if (s.includes(":")) return s;
    if (TV_INDICES[s]) return TV_INDICES[s];
    if (s.endsWith("=F")) return TV_FUTURES[s.slice(0, -2)] || `${s.slice(0, -2)}1!`;
    if (s.endsWith("=X")) return `FX:${s.slice(0, -2)}`;
    if (/^[A-Z0-9]+-USDT?$/.test(s)) return s.replace("-", "");
    if (/^[A-Z]+-[A-Z]$/.test(s)) return s.replace("-", "."); // share class: BRK-B → BRK.B
    const m = s.match(/^(.+)\.([A-Z]{1,2})$/);
    if (m && TV_SUFFIX[m[2]]) return `${TV_SUFFIX[m[2]]}:${m[1]}`;
    return s.replace(/^\^/, "");
  }
  function updateTradingViewLink(symbol, interval) {
    const a = $("tvLink");
    const tv = tradingViewSymbol(symbol);
    a.href = `https://www.tradingview.com/chart/?${new URLSearchParams({ symbol: tv, interval: TV_INTERVAL[interval] || "D" })}`;
    a.title = `Open ${tv} on tradingview.com in a new tab`;
    a.hidden = false;
  }

  async function load(symbol, interval, period, push = true) {
    symbol = symbol.trim().toUpperCase();
    if (!symbol) return;
    const btn = els.form.querySelector("button");
    btn.disabled = true;
    setStatus(`Loading <b>${escapeHtml(symbol)}</b>…`);
    try {
      const q = new URLSearchParams({ symbol, interval, period });
      const res = await fetch(`/api/analyze?${q}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      state.data = body;
      state.selected = null;
      let saved = "";
      try { saved = localStorage.getItem(buyKey()) || ""; } catch (_) {}
      $("buyPrice").value = saved;
      if (push) history.pushState(null, "", `?${q}`);
      document.title = `${body.symbol} · Market Lens`;
      els.symbol.value = body.symbol;

      candles.setData(body.candles.map(({ volume: _v, ...c }) => c));
      const smaPts = (body.sma && body.sma.points) || [];
      sma.setData(smaPts);
      sma.applyOptions({ title: body.sma ? `SMA ${body.sma.length}` : "" });
      state.smaByTime = new Map(smaPts.map((p) => [p.time, p.value]));
      const usdPts = (body.usd && body.usd.points) || [];
      usd.setData(usdPts.map(({ time, value }) => ({ time, value })));
      state.usdByTime = new Map(usdPts.map((p) => [p.time, p]));
      const up = css("--bull") + "66", down = css("--bear") + "66";
      volume.setData(body.candles.map((c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? up : down })));
      chart.timeScale().applyOptions({ timeVisible: interval === "1h", secondsVisible: false });
      defaultView();

      const cs = body.candles, last = cs[cs.length - 1], prev = cs[cs.length - 2];
      els.title.textContent = `${body.name} (${body.symbol})`;
      const lastUsd = usdPts.length ? usdPts[usdPts.length - 1] : null;
      els.subtitle.textContent = [body.exchange, body.currency, `${cs.length} bars`, `last ${fmt(last.close)}`,
        lastUsd && `≈ $${fmt(lastUsd.value)} at ${fmt(lastUsd.rate)} EGP/$`].filter(Boolean).join(" · ");
      computeIndicators();
      applyIndicators();
      updateLegend(last, prev, cs.length - 1);
      setMeasureMode(false);
      setAlertMode(false);
      alertPrice.value = "";
      alertUi.list = [];
      loadAlerts();
      renderOutlook(body.outlook);
      renderMain();
      $("chartTools").hidden = false;
      render();
      setStatus("");
      updateTradingViewLink(body.symbol, interval);
      renderHalal();
      renderProfileButton(symbol);
      renderChecklist();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- measure tool ----------
  // Two clicks span a box showing the price change, % and time between them. Points are kept as
  // { logical bar index, price } so the box stays on the bars when the chart is zoomed or panned.
  const measure = { on: false, a: null, b: null, live: null };
  const layer = $("measureLayer");
  let shiftClick = false;
  els.chart.addEventListener("pointerdown", (e) => { shiftClick = e.shiftKey; }, true);

  function setMeasureMode(on) {
    if (on && alertUi.on) setAlertMode(false); // one click-to-pick tool at a time
    measure.on = on;
    if (!on) measure.a = measure.b = measure.live = null;
    const btn = $("measureBtn");
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "⟷ Measuring · Esc to stop" : "⟷ Measure";
    els.chart.classList.toggle("measuring", on);
    drawMeasure();
  }

  function pointAt(param) {
    if (!param.point || param.logical == null) return null;
    const price = candles.coordinateToPrice(param.point.y);
    return price == null ? null : { logical: Math.round(param.logical), price };
  }

  // Bar time in seconds; past the last bar it's extrapolated at the average bar spacing.
  function barSeconds(i) {
    const cs = state.data.candles, secs = (t) => (typeof t === "number" ? t : Date.parse(t) / 1000);
    const first = secs(cs[0].time), last = secs(cs[cs.length - 1].time), step = (last - first) / Math.max(1, cs.length - 1);
    return i < 0 ? first + i * step : i >= cs.length ? last + (i - cs.length + 1) * step : secs(cs[i].time);
  }

  function spanText(a, b) {
    const bars = Math.abs(b.logical - a.logical);
    const days = Math.abs(barSeconds(b.logical) - barSeconds(a.logical)) / 86400;
    const span = days < 2 ? `${Math.round(days * 24)} hours` : days < 60 ? `${Math.round(days)} days`
      : days < 730 ? `${(days / 30.44).toFixed(1)} months` : `${(days / 365.25).toFixed(1)} years`;
    return `${bars} bar${bars === 1 ? "" : "s"} · ${span}`;
  }

  function drawMeasure() {
    layer.replaceChildren();
    const a = measure.a, b = measure.b || measure.live;
    if (!state.data || !a || !b) return;
    const ts = chart.timeScale(), off = usdShown() ? chart.priceScale("left").width() : 0; // the USD axis pushes the plot right
    const x1 = off + ts.logicalToCoordinate(a.logical), x2 = off + ts.logicalToCoordinate(b.logical);
    const y1 = candles.priceToCoordinate(a.price), y2 = candles.priceToCoordinate(b.price);
    if ([x1, x2, y1, y2].some((v) => v == null)) return;
    const color = css(b.price >= a.price ? "--bull" : "--bear");
    const add = (tag, attrs, text) => {
      const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text) n.textContent = text;
      layer.appendChild(n);
      return n;
    };
    add("rect", { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
                  fill: color, "fill-opacity": 0.12, stroke: color, "stroke-opacity": 0.45 });
    add("line", { x1, y1, x2, y2, stroke: color, "stroke-width": 1.5, "stroke-dasharray": "5 4" });
    add("circle", { cx: x1, cy: y1, r: 3.5, fill: color });
    add("circle", { cx: x2, cy: y2, r: 3.5, fill: color });

    const chg = b.price - a.price, pct = (chg / a.price) * 100, sign = (v) => (v >= 0 ? "+" : "−");
    const d = decimals(a.price); // the change gets the price's decimals, not its own (0.35 would show as 0.35000)
    const amount = Math.abs(chg).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
    const rows = [`${sign(chg)}${amount} (${sign(pct)}${Math.abs(pct).toFixed(2)}%)`, spanText(a, b)];
    const font = { "font-family": css("--font"), "font-size": 12 };
    const label = add("text", { ...font, fill: css("--text"), "text-anchor": "middle" });
    rows.forEach((r, i) => {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "tspan");
      t.setAttribute("x", 0);
      t.setAttribute("dy", i ? 16 : 0);
      if (!i) t.setAttribute("font-weight", 700);
      t.textContent = r;
      label.appendChild(t);
    });
    // Box the label just beyond the end it points to, kept inside the plot area.
    const bb = label.getBBox(), w = bb.width + 16, h = bb.height + 10;
    const plotW = ts.width(), plotH = els.chart.clientHeight - ts.height();
    const above = b.price >= a.price;
    const cx = Math.min(Math.max((x1 + x2) / 2, off + w / 2 + 4), off + plotW - w / 2 - 4);
    let top = above ? Math.min(y1, y2) - h - 8 : Math.max(y1, y2) + 8;
    top = Math.min(Math.max(top, 4), plotH - h - 4);
    label.querySelectorAll("tspan").forEach((t) => t.setAttribute("x", cx));
    label.setAttribute("y", top + 5 - bb.y);
    const box = add("rect", { x: cx - w / 2, y: top, width: w, height: h, rx: 6, fill: css("--panel"), stroke: color, "stroke-width": 1 });
    layer.insertBefore(box, label);
  }

  chart.subscribeClick((param) => {
    if (!state.data || !(measure.on || shiftClick)) return;
    const p = pointAt(param);
    if (!p) return;
    if (!measure.on) setMeasureMode(true);
    if (!measure.a || measure.b) {
      measure.a = p;
      measure.b = null;
      measure.live = p;
    } else {
      measure.b = p;
    }
    drawMeasure();
  });
  chart.subscribeCrosshairMove((param) => {
    if (measure.on && measure.a && !measure.b) {
      measure.live = pointAt(param) || measure.live;
      drawMeasure();
    }
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => measure.a && drawMeasure());
  new ResizeObserver(() => measure.a && drawMeasure()).observe(els.chart);
  $("measureBtn").addEventListener("click", () => setMeasureMode(!measure.on));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && measure.on) setMeasureMode(false);
  });

  // ---------- price alerts ----------
  // Pick a price on the chart (or type one) and save it: the server checks every minute and sends a
  // Telegram message when price gets there. Waiting alerts are drawn as dashed lines.
  const alertUi = { on: false, list: [], telegram: false, pending: null };
  const alertBox = $("alertBox"), alertPrice = $("alertPrice");

  async function loadAlerts() {
    const d = state.data;
    try {
      const body = await (await fetch(`/api/price-alerts?${new URLSearchParams({ symbol: d.symbol })}`)).json();
      if (state.data !== d) return;
      alertUi.list = body.alerts;
      alertUi.telegram = body.telegram;
    } catch (_) {
      alertUi.list = [];
    }
    renderAlerts();
    render();
  }

  function renderAlerts() {
    const waiting = alertUi.list.filter((a) => !a.triggeredAt);
    $("alertBtn").textContent = waiting.length ? `🔔 Alert (${waiting.length})` : "🔔 Alert";
    const day = (iso) => iso.slice(0, 10);
    $("alertList").innerHTML = alertUi.list.slice().reverse().map((a) => `
      <li class="${a.triggeredAt ? "done" : ""}" data-id="${a.id}">
        <b>${a.direction === "above" ? "▲" : "▼"} ${fmt(a.price)}</b>
        <span class="al-note">${a.triggeredAt ? `✓ reached ${fmt(a.triggeredPrice)} on ${day(a.triggeredAt)}${a.sent ? " · sent" : ""}` : escapeHtml(a.note || `set at ${fmt(a.createdPrice)}`)}</span>
        ${a.error ? `<span class="al-err" title="${escapeHtml(a.error)}">⚠</span>` : ""}
        <button type="button" data-del="${a.id}" title="Delete this alert" aria-label="Delete">✕</button>
      </li>`).join("");
    updateAlertHint();
  }

  function updateAlertHint() {
    const hint = $("alertHint"), p = Number(alertPrice.value);
    hint.classList.toggle("warn", !alertUi.telegram);
    if (!alertUi.telegram) {
      hint.innerHTML = 'Telegram isn\'t set up, so alerts can\'t reach you yet: add the bot in <a href="/profile">My profile → Alerts</a>.';
    } else if (p > 0 && state.data) {
      const last = state.data.candles[state.data.candles.length - 1].close;
      hint.textContent = `A Telegram message when price ${p > last ? "rises" : "falls"} to ${fmt(p)} (now ${fmt(last)}). Checked every few minutes.`;
    } else {
      hint.textContent = "Checked every few minutes, at any hour, while the app is running.";
    }
    // a dashed preview line where the new alert would go
    if (alertUi.pending) candles.removePriceLine(alertUi.pending);
    alertUi.pending = alertUi.on && p > 0
      ? candles.createPriceLine({ price: p, color: css("--accent"), lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: "🔔 new" })
      : null;
  }

  function setAlertMode(on) {
    alertUi.on = on;
    if (on) setMeasureMode(false);
    alertBox.hidden = !on;
    $("alertBtn").setAttribute("aria-pressed", String(on));
    els.chart.classList.toggle("alerting", on);
    if (on && !alertPrice.value && state.data) alertPrice.value = state.data.candles[state.data.candles.length - 1].close;
    updateAlertHint();
  }

  chart.subscribeClick((param) => {
    if (!alertUi.on || !param.point) return;
    const p = candles.coordinateToPrice(param.point.y);
    if (p == null || p <= 0) return;
    const d = decimals(p);
    alertPrice.value = Number(p.toFixed(d));
    updateAlertHint();
  });
  $("alertBtn").addEventListener("click", () => state.data && setAlertMode(!alertUi.on));
  $("alertClose").addEventListener("click", () => setAlertMode(false));
  alertPrice.addEventListener("input", updateAlertHint);
  alertBox.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = alertBox.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await fetch("/api/price-alerts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: state.data.symbol, name: state.data.name, price: Number(alertPrice.value), note: $("alertNote").value }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || `Request failed (${res.status})`);
      $("alertNote").value = "";
      await loadAlerts();
    } catch (err) {
      $("alertHint").textContent = err.message;
      $("alertHint").classList.add("warn");
    } finally {
      btn.disabled = false;
    }
  });
  $("alertList").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (!del) return;
    await fetch(`/api/price-alerts/${del.dataset.del}`, { method: "DELETE" });
    loadAlerts();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && alertUi.on && !e.target.closest("input")) setAlertMode(false);
  });

  // ---------- full screen ----------
  // The whole chart panel (title, main pattern, legend, chart) fills the screen; a fixed overlay stands in
  // where the browser has no Fullscreen API for page elements.
  const panel = document.querySelector(".chart-panel");
  const isFull = () => document.fullscreenElement === panel || panel.classList.contains("full");
  let keepRange = null; // the bars in view, kept across the resize so they spread over the new width
  function syncFull() {
    const on = isFull(), btn = $("fullBtn");
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "⛶ Exit full screen" : "⛶ Full screen";
    document.body.classList.toggle("no-scroll", panel.classList.contains("full"));
    // Leaving the browser's full screen with Esc skips toggleFull; the chart hasn't resized yet at this point.
    const range = keepRange || (state.data && chart.timeScale().getVisibleLogicalRange());
    keepRange = null;
    // the chart resizes itself on the next frames; put the same bars back once it has
    if (range) setTimeout(() => chart.timeScale().setVisibleLogicalRange(range), 80);
  }
  function toggleFull() {
    keepRange = chart.timeScale().getVisibleLogicalRange();
    if (isFull()) {
      if (document.fullscreenElement) document.exitFullscreen();
      panel.classList.remove("full");
    } else if (panel.requestFullscreen && document.fullscreenEnabled) {
      panel.requestFullscreen().catch(() => panel.classList.add("full")).finally(syncFull);
      return;
    } else {
      panel.classList.add("full");
    }
    syncFull();
  }
  $("fullBtn").addEventListener("click", toggleFull);
  document.addEventListener("fullscreenchange", syncFull);
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest("input, select, textarea, [contenteditable]");
    if (e.key === "Escape" && panel.classList.contains("full") && !measure.on) {
      panel.classList.remove("full");
      syncFull();
    } else if ((e.key === "f" || e.key === "F") && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && state.data) {
      toggleFull();
    }
  });

  // ---------- events ----------
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    load(els.symbol.value, els.interval.value, els.period.value);
  });
  els.interval.addEventListener("change", () => state.data && load(state.data.symbol, els.interval.value, els.period.value));
  els.period.addEventListener("change", () => state.data && load(state.data.symbol, els.interval.value, els.period.value));
  document.querySelectorAll("[data-cat]").forEach((cb) => cb.addEventListener("change", () => state.data && render()));
  els.minConf.addEventListener("input", () => {
    els.minConfOut.textContent = `${els.minConf.value}%`;
    if (state.data) render();
  });
  els.list.addEventListener("click", (e) => {
    if (e.target.closest("[data-show]")) {
      els.showMode.value = "all";
      els.showMode.dispatchEvent(new Event("change"));
      return;
    }
    const li = e.target.closest("li[data-id]");
    if (li) selectPattern(Number(li.dataset.id));
  });
  els.showMode.addEventListener("change", () => {
    try { localStorage.setItem("showMode", els.showMode.value); } catch (_) {}
    if (state.data) render();
  });
  $("mainPattern").addEventListener("click", (e) => selectPattern(Number(e.currentTarget.dataset.id)));
  $("fitBtn").addEventListener("click", () => {
    state.selected = null;
    if (state.data) render();
    chart.priceScale("right").applyOptions({ autoScale: true });
    defaultView();
  });
  $("shotBtn").addEventListener("click", () => state.data && snapshot());

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  els.theme.addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
    applyTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  function loadFromUrl() {
    const q = new URLSearchParams(location.search);
    if (q.get("interval")) els.interval.value = q.get("interval");
    if (q.get("period")) els.period.value = q.get("period");
    if (q.get("symbol")) load(q.get("symbol"), els.interval.value, els.period.value, false);
  }
  window.addEventListener("popstate", loadFromUrl);

  applyTheme();
  loadFromUrl();
})();
