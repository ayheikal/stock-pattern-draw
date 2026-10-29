/* global LightweightCharts */
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    form: $("search"), symbol: $("symbol"), interval: $("interval"), period: $("period"),
    chart: $("chart"), status: $("status"), title: $("title"), subtitle: $("subtitle"),
    legend: $("legend"), list: $("patternList"), levels: $("levelList"), count: $("count"),
    minConf: $("minConf"), minConfOut: $("minConfOut"), theme: $("theme"),
  };

  const state = { data: null, selected: null, overlays: [], priceLines: [] };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- chart setup ----------
  const chart = LightweightCharts.createChart(els.chart, {
    autoSize: true,
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, rightOffset: 8 },
  });
  const candles = chart.addCandlestickSeries({ borderVisible: true, priceLineVisible: false });
  const sma = chart.addLineSeries({ lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false, title: "SMA 200" });
  const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  const logScale = $("logScale");
  try { const v = localStorage.getItem("logScale"); if (v != null) logScale.checked = v === "1"; } catch (_) {}
  function applyScaleMode() {
    const { Logarithmic, Normal } = LightweightCharts.PriceScaleMode;
    chart.priceScale("right").applyOptions({ mode: logScale.checked ? Logarithmic : Normal });
  }
  logScale.addEventListener("change", () => {
    applyScaleMode();
    try { localStorage.setItem("logScale", logScale.checked ? "1" : "0"); } catch (_) {}
  });
  applyScaleMode();

  function applyTheme() {
    const text = css("--text"), grid = css("--grid");
    chart.applyOptions({
      layout: { background: { type: "solid", color: css("--panel") }, textColor: css("--muted"), fontFamily: css("--font") },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      crosshair: { vertLine: { labelBackgroundColor: text }, horzLine: { labelBackgroundColor: text } },
    });
    // Hollow up candles, filled down candles, one ink color (TradingView "hollow" style).
    const ink = css("--candle");
    candles.applyOptions({
      upColor: css("--panel"), downColor: ink,
      borderUpColor: ink, borderDownColor: ink,
      wickUpColor: ink, wickDownColor: ink,
    });
    sma.applyOptions({ color: css("--sma") });
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

  function visiblePatterns() {
    if (!state.data) return [];
    return state.data.patterns.filter((p) => catEnabled(p.category) && (p.category === "candle" || p.confidence >= minConf()));
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
      ? { lastValueVisible: true, title: /Cup/.test(p.name) ? "Rim" : "Neckline" }
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
      if (p.category === "candle") {
        if (dim) continue;
        const bull = p.direction === "bullish";
        markers.push({
          time: p.endTime,
          position: bull ? "belowBar" : "aboveBar",
          shape: p.direction === "neutral" ? "circle" : bull ? "arrowUp" : "arrowDown",
          color, text: p.label, size: sel ? 1.4 : 0.8,
        });
        continue;
      }
      const w = sel ? 3 : dim ? 1 : 2;
      const c = dim ? color + "55" : color;
      p.lines.forEach((ln, i) => {
        const isTarget = p.target != null && i === p.lines.length - 1;
        addLine(ln, c, isTarget ? 1 : w, isTarget ? Dotted : Solid);
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

    const order = new Map(state.data.candles.map((c, i) => [c.time, i]));
    markers.sort((a, b) => order.get(a.time) - order.get(b.time));
    candles.setMarkers(markers);
    renderList(pats);
  }

  function renderList(pats) {
    const chartPats = pats.filter((p) => p.category !== "candle").reverse();
    const candlePats = pats.filter((p) => p.category === "candle").reverse();
    els.count.textContent = chartPats.length + candlePats.length;
    const item = (p) => `
      <li data-id="${p.id}" class="${p.direction}${state.selected === p.id ? " selected" : ""}">
        <div class="row"><span class="name">${escapeHtml(p.name)}</span><span class="tag ${p.direction}">${p.direction}</span></div>
        <div class="meta">${timeLabel(p.startTime)} → ${timeLabel(p.endTime)} · ${p.category}${p.confirmed ? " · confirmed" : p.category === "reversal" || p.category === "continuation" ? " · forming" : ""}</div>
        ${p.target != null ? `<div class="meta">Target ≈ ${fmt(p.target)}</div>` : ""}
        ${p.category !== "candle" ? `<div class="confbar" title="Confidence ${Math.round(p.confidence * 100)}%"><span style="width:${p.confidence * 100}%"></span></div>` : ""}
      </li>`;
    els.list.innerHTML = chartPats.length + candlePats.length
      ? chartPats.map(item).join("") + candlePats.map(item).join("")
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
    if (!o) return;
    const b = $("outlookBias");
    b.textContent = o.bias;
    b.className = "bias " + (/bullish/i.test(o.bias) ? "bullish" : /bearish/i.test(o.bias) ? "bearish" : "");
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

  // ---------- buy checklist (/api/decision) ----------
  const CHECK_ICON = { pass: "✓", warn: "!", fail: "✗", unknown: "?" };
  async function renderChecklist() {
    const box = $("checklist"), d = state.data;
    box.hidden = true;
    $("rating").hidden = true;
    if (!d.currency || !["1d", "1wk"].includes(d.interval)) return; // indices and intraday bars: no checklist
    let c;
    try {
      const r = await fetch(`/api/decision?${new URLSearchParams({ symbol: d.symbol, interval: d.interval })}`);
      if (!r.ok) return;
      c = await r.json();
    } catch (_) { return; }
    if (state.data !== d || !c.applicable) return; // another symbol loaded meanwhile
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

  function updateLegend(c, prev) {
    if (!c) { els.legend.innerHTML = ""; return; }
    const chg = prev ? ((c.close - prev.close) / prev.close) * 100 : 0;
    const cls = chg >= 0 ? "up" : "down";
    const m = state.smaByTime && state.smaByTime.get(c.time);
    els.legend.innerHTML = `${timeLabel(c.time)} &nbsp; O ${fmt(c.open)} H ${fmt(c.high)} L ${fmt(c.low)} C <span class="${cls}">${fmt(c.close)} (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)</span> &nbsp; Vol ${fmtVol(c.volume)}`
      + (m != null ? ` &nbsp; <span class="sma">SMA ${state.data.sma.length} ${fmt(m)}</span>` : "");
  }

  chart.subscribeCrosshairMove((param) => {
    if (!state.data) return;
    const cs = state.data.candles;
    const i = param.logical != null ? Math.max(0, Math.min(cs.length - 1, Math.round(param.logical))) : cs.length - 1;
    updateLegend(cs[i], cs[i - 1]);
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
      const up = css("--bull") + "66", down = css("--bear") + "66";
      volume.setData(body.candles.map((c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? up : down })));
      chart.timeScale().applyOptions({ timeVisible: interval === "1h", secondsVisible: false });
      chart.timeScale().fitContent();

      const cs = body.candles, last = cs[cs.length - 1], prev = cs[cs.length - 2];
      els.title.textContent = `${body.name} (${body.symbol})`;
      els.subtitle.textContent = [body.exchange, body.currency, `${cs.length} bars`, `last ${fmt(last.close)}`].filter(Boolean).join(" · ");
      updateLegend(last, prev);
      renderOutlook(body.outlook);
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
    const li = e.target.closest("li[data-id]");
    if (li) selectPattern(Number(li.dataset.id));
  });

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
