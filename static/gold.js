/* global LightweightCharts */
(() => {
  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const tone = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const state = { data: null, unit: "21", months: 12, chart: null, line: null };
  const UNITS = { 24: "1 g of 24k, EGP", 21: "1 g of 21k, EGP", 18: "1 g of 18k, EGP", pound: "Gold pound, EGP", usd: "1 ounce, USD", rate: "USD/EGP" };

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  // One day's value in the chosen unit, from the three stored figures.
  function inUnit(p, unit, d) {
    if (unit === "usd") return p.usd;
    if (unit === "rate") return p.rate;
    if (unit === "pound") return p.gram24 * d.karats["21"] * d.poundGrams;
    return p.gram24 * d.karats[unit];
  }

  function tile(title, now, before, note, digits = 0, unit = "EGP") {
    const ch = before ? (now / before - 1) * 100 : null;
    return `<div class="tile"><h3>${escapeHtml(title)}</h3>
      <div class="big">${money(now, digits)} <span class="unit">${unit}</span></div>
      <div class="${tone(ch)}">${pct(ch)} <span class="muted">on the day</span></div>
      <div class="muted small">${escapeHtml(note)}</div></div>`;
  }

  function renderTiles(d) {
    const p = d.prices, q = d.previous;
    $("tiles").innerHTML = tile("21k gram", p.gram["21"], q.gram["21"], "the karat most jewellery and bars are quoted in")
      + tile("24k gram", p.gram["24"], q.gram["24"], "pure gold: bullion bars")
      + tile("18k gram", p.gram["18"], q.gram["18"], "75% gold")
      + tile("Gold pound", p.pound, q.pound, "8 g of 21k")
      + tile("Ounce, world price", p.ounceUsd, q.ounceUsd, "what every other figure starts from", 2, "USD")
      + tile("US dollar", p.rate, q.rate, "pounds for one dollar: up means the pound fell", 2, "EGP");
  }

  function renderChanges(d) {
    $("explain").innerHTML = `<ul><li class="flat">${escapeHtml(d.explain)}</li></ul>`;
    $("changes").querySelector("tbody").innerHTML = d.changes.map((r) => {
      const better = r.stocks == null ? "" : Math.abs(r.egp - r.stocks) < 0.5 ? '<span class="muted">about the same</span>'
        : r.egp > r.stocks ? '<span class="verdict-pill none">Gold</span>' : '<span class="verdict-pill useful">Stocks</span>';
      return `<tr><td>${escapeHtml(r.label)} <span class="muted small">since ${escapeHtml(r.from)}</span></td>
        <td class="num ${tone(r.egp)}"><b>${pct(r.egp)}</b></td>
        <td class="num ${tone(r.usd)}">${pct(r.usd)}</td>
        <td class="num ${tone(r.rate)}">${pct(r.rate)}</td>
        <td class="num ${tone(r.stocks)}">${pct(r.stocks)}</td>
        <td>${better}</td></tr>`;
    }).join("");
  }

  function renderTrend(d) {
    const t = d.trendEgp, u = d.trendUsd;
    const cls = { Uptrend: "good", Downtrend: "bad" };
    const line = (name, x, digits, unit) => `<b>${name}: ${escapeHtml(x.trend)}.</b> `
      + (x.above200 == null ? "" : `${x.above200 ? "Above" : "Below"} its 200-day average (${pct(x.vs200Pct)}), ${x.above50 ? "above" : "below"} its 50-day. `)
      + `One-year range ${money(x.low, digits)} to ${money(x.high, digits)} ${unit}; now ${pct(x.offHighPct)} from the high.`;
    $("trend").innerHTML = `<ul><li class="${cls[t.trend] || "flat"}">${line("21k gram in pounds", t, 0, "EGP")}</li>
      <li class="${cls[u.trend] || "flat"}">${line("Ounce in dollars", u, 0, "USD")}</li></ul>`;
  }

  // ---- chart ----
  function chartColors() {
    return { layout: { background: { color: "transparent" }, textColor: css("--muted"), fontFamily: "Inter, sans-serif" },
      grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
      rightPriceScale: { borderColor: css("--border") }, timeScale: { borderColor: css("--border") } };
  }

  function drawChart() {
    const d = state.data;
    if (!state.chart) {
      state.chart = LightweightCharts.createChart($("chart"), { autoSize: true, ...chartColors(), handleScale: { axisPressedMouseMove: false } });
      state.line = state.chart.addAreaSeries({ lineWidth: 2 });
    }
    const usd = state.unit === "usd" || state.unit === "rate";
    const color = css(state.unit === "rate" ? "--usd" : "--resistance");
    state.line.applyOptions({ lineColor: color, topColor: color + "44", bottomColor: color + "05", title: UNITS[state.unit],
      priceFormat: { type: "price", precision: usd ? 2 : 0, minMove: usd ? 0.01 : 1 } });
    state.line.setData(d.series.map((p) => ({ time: p.time, value: inUnit(p, state.unit, d) })));
    const last = new Date(d.series[d.series.length - 1].time);
    if (state.months) {
      const from = new Date(last);
      from.setMonth(from.getMonth() - state.months);
      const first = d.series[0].time, iso = from.toISOString().slice(0, 10);
      state.chart.timeScale().setVisibleRange({ from: iso < first ? first : iso, to: d.series[d.series.length - 1].time });
    } else state.chart.timeScale().fitContent();
  }

  function segment(id, attr, apply) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      apply(b.dataset[attr]);
      drawChart();
      save();
    });
  }

  // ---- the two calculators (worked out here, from the prices on the page) ----
  function localCheck() {
    const d = state.data, price = parseFloat($("localPrice").value), k = $("localKarat").value;
    if (!d || !(price > 0)) {
      $("localOut").innerHTML = "Type the price a shop or an app quotes, without workmanship, to see how far it is from the world price.";
      return;
    }
    const world = d.prices.gram[k];
    const prem = (price / world - 1) * 100;
    const implied = price / (d.prices.ounceUsd / d.ounceGrams * d.karats[k]);
    const read = Math.abs(prem) < 1 ? "in line with the world price"
      : prem > 0 ? `<b class="down">${prem.toFixed(1)}% above</b> the world price: you pay a premium, and it can vanish if the local market calms`
        : `<b class="up">${Math.abs(prem).toFixed(1)}% below</b> the world price`;
    $("localOut").innerHTML = `World price for ${k}k is <b>${money(world)}</b> EGP a gram, so ${money(price)} is ${read}. `
      + `That price values the dollar at <b>${implied.toFixed(2)}</b> EGP, against the bank rate of ${d.prices.rate.toFixed(2)}.`;
  }

  function myGold() {
    const d = state.data, grams = parseFloat($("myGrams").value), paid = parseFloat($("myPaid").value), k = $("myKarat").value;
    const cut = Math.max(0, parseFloat($("myDiscount").value) || 0);
    if (!d || !(grams > 0)) {
      $("myOut").innerHTML = "Enter what you hold to see what it is worth now and your gain or loss in pounds.";
      return;
    }
    const world = d.prices.gram[k], value = grams * world, sell = value * (1 - cut / 100);
    let html = `${money(grams, 2)} g of ${k}k is worth about <b>${money(value)}</b> EGP at the world price, or <b>${money(sell)}</b> if sold back ${cut}% under it.`;
    if (paid > 0) {
      const cost = grams * paid, gain = sell - cost;
      html += ` You paid ${money(cost)}, so selling now ${gain >= 0 ? "makes" : "loses"} <b class="${tone(gain)}">${money(Math.abs(gain))} EGP (${pct(gain / cost * 100)})</b>. `
        + `Break-even is a world price of ${money(paid / (1 - cut / 100))} EGP a gram.`;
    }
    $("myOut").innerHTML = html;
  }

  const FIELDS = ["localKarat", "localPrice", "myKarat", "myGrams", "myPaid", "myDiscount"];
  function save() {
    try {
      localStorage.setItem("gold", JSON.stringify({ unit: state.unit, months: state.months, ...Object.fromEntries(FIELDS.map((f) => [f, $(f).value])) }));
    } catch (_) {}
  }
  function restore() {
    try {
      const s = JSON.parse(localStorage.getItem("gold") || "{}");
      for (const f of FIELDS) if (s[f] != null && s[f] !== "") $(f).value = s[f];
      if (s.unit && UNITS[s.unit]) state.unit = s.unit;
      if (s.months != null) state.months = Number(s.months);
      $("unit").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.u === state.unit));
      $("range").querySelectorAll("button").forEach((b) => b.classList.toggle("on", Number(b.dataset.m) === state.months));
    } catch (_) {}
  }

  async function load() {
    try {
      const res = await fetch("/api/gold");
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || `Request failed (${res.status})`);
      state.data = d;
      $("report").hidden = false;
      $("meta").textContent = `gold as of ${d.goldDate} · dollar as of ${d.rateDate}`;
      $("source").textContent = `${d.source}, world spot price`;
      renderTiles(d);
      renderChanges(d);
      renderTrend(d);
      drawChart();
      localCheck();
      myGold();
      setStatus("");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
  }

  restore();
  segment("unit", "u", (v) => { state.unit = v; });
  segment("range", "m", (v) => { state.months = Number(v); });
  for (const f of FIELDS) $(f).addEventListener("input", () => { localCheck(); myGold(); save(); });
  for (const id of ["local", "mine"]) $(id).addEventListener("submit", (e) => e.preventDefault());

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
    if (state.chart) { state.chart.applyOptions(chartColors()); drawChart(); }
  });

  load();
})();
