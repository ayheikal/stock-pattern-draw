/* global LightweightCharts */
(() => {
  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const tone = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const state = { data: null, unit: "21", months: 12, chart: null, line: null, fibLines: [], ewLines: [] };
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

  // ---- buy decision: the server's checks, plus the local premium when a shop price is typed in ----
  const CHECK_ICON = { pass: "✓", warn: "!", fail: "✗", unknown: "?" };
  const VERDICTS = { buy: ["Good time to buy", "useful"], wait: ["Wait", "none"], avoid: ["Hold off", "misleading"] };
  const LOCAL_PREMIUM_WARN = 3; // % above the world price
  // Same rule as gold.verdict_of (change both together): 2+ fails hold off, 1 fail or 2+ warnings wait, else buy.
  const verdictOf = (checks) => {
    const fails = checks.filter((c) => c.status === "fail").length, warns = checks.filter((c) => c.status === "warn").length;
    return fails >= 2 ? "avoid" : fails || warns >= 2 ? "wait" : "buy";
  };

  function localPremium() {
    const d = state.data, price = parseFloat($("localPrice").value), k = $("localKarat").value;
    return d && price > 0 ? (price / d.prices.gram[k] - 1) * 100 : null;
  }

  function renderDecision() {
    const dec = state.data && state.data.decision;
    $("decision").hidden = !dec;
    if (!dec) return;
    const checks = dec.checks.slice();
    const prem = localPremium();
    if (prem != null) {
      const high = prem > LOCAL_PREMIUM_WARN;
      checks.push({ key: "local", label: "The local price is close to the world price", status: high ? "warn" : "pass",
        detail: high ? `The price you typed is ${prem.toFixed(1)}% above the world price: you'd pay a premium that can vanish if the local market calms.`
          : `The price you typed is ${prem >= 0 ? `${prem.toFixed(1)}% above` : `${Math.abs(prem).toFixed(1)}% below`} the world price.` });
    }
    const v = prem == null ? dec.verdict : verdictOf(checks);
    const [label, cls] = VERDICTS[v];
    $("decisionPill").className = `verdict-pill ${cls}`;
    $("decisionPill").textContent = label;
    $("decisionReason").textContent = v === dec.verdict ? dec.reason
      : `${dec.reason} With the local price you typed, it's ${label.toLowerCase()}: ${checks[checks.length - 1].detail}`;
    $("decisionChecks").innerHTML = checks.map((c) => `<li class="${c.status}"><span class="ic">${CHECK_ICON[c.status]}</span>
      <span><b>${escapeHtml(c.label)}</b> <span class="muted">${escapeHtml(c.detail)}</span></span></li>`).join("");
  }

  function renderTrend(d) {
    const t = d.trendEgp, u = d.trendUsd, r = d.trendRate;
    const cls = { Uptrend: "good", Downtrend: "bad" };
    const line = (name, x, digits, unit) => `<b>${name}: ${escapeHtml(x.trend)}.</b> `
      + (x.above200 == null ? "" : `${x.above200 ? "Above" : "Below"} its 200-day average (${pct(x.vs200Pct)}), ${x.above50 ? "above" : "below"} its 50-day. `)
      + `One-year range ${money(x.low, digits)} to ${money(x.high, digits)} ${unit}; now ${pct(x.offHighPct)} from the high.`;
    $("trend").innerHTML = `<ul><li class="${cls[t.trend] || "flat"}">${line("21k gram in pounds", t, 0, "EGP")}</li>
      <li class="${cls[u.trend] || "flat"}">${line("Ounce in dollars", u, 0, "USD")}</li>
      ${r ? `<li class="${cls[r.trend] || "flat"}">${line("Dollar in pounds (up = weaker pound, lifts gold in pounds)", r, 2, "EGP")}</li>` : ""}</ul>`;
  }

  // ---- Fibonacci: the server measures 24k gram in EGP, the ounce in USD and USD/EGP; the karats are scaled ----
  const FIB_READS = {
    top: ["flat", "Near the top of the last rise: buying now chases it. A dip into the buy zone is a better entry; a limit order near the 50% level is one way."],
    shallow: ["good", "A shallow pullback in the rise. A reasonable entry; the buy zone below has firmer support."],
    zone: ["good", "Pulled back into the 38.2–61.8% buy zone, where rises often resume. A reasonable entry, with the stop below the 78.6% level."],
    deep: ["flat", "A deep pullback: past 61.8% but still above 78.6%. The rise is weakening; buy in parts at most, and step aside below the 78.6% level."],
    broken: ["bad", "Gave back more than 78.6% of the rise: it has likely failed. Wait for a new swing before buying."],
    falling: ["bad", "The last big move was a fall and the price is still near its low. Wait for a close above the 61.8% level before buying."],
    bounce: ["flat", "Bouncing after a fall, into the levels where falls often resume. Wait for a close above the 61.8% level, better the 78.6%."],
    reclaimed: ["good", "Took back most of the last fall: buyers are back in control."],
  };

  function fibFor(unit, d) {
    const f = d.fib && d.fib[unit === "usd" || unit === "rate" ? unit : "egp"];
    if (!f) return null;
    const k = unit === "usd" || unit === "rate" ? 1 : inUnit({ gram24: 1 }, unit, d);
    const x = (v) => (v == null ? null : v * k);
    return { ...f, high: x(f.high), low: x(f.low), entry: x(f.entry), stop: x(f.stop), target: x(f.target),
      zone: { low: x(f.zone.low), high: x(f.zone.high) },
      levels: f.levels.map((l) => ({ ratio: l.ratio, price: x(l.price) })), extensions: f.extensions.map((l) => ({ ratio: l.ratio, price: x(l.price) })) };
  }

  const fibPct = (r) => `${(r * 100).toFixed(1).replace(/\.0$/, "")}%`;

  function renderFib() {
    const d = state.data, f = fibFor(state.unit, d), box = $("fib");
    const digits = state.unit === "rate" ? 2 : 0, unit = state.unit === "usd" ? "USD" : "EGP";
    const name = { 24: "a 24k gram", 21: "a 21k gram", 18: "an 18k gram", pound: "a gold pound", usd: "an ounce", rate: "the dollar" }[state.unit];
    if (!f) {
      box.innerHTML = `<ul><li class="flat">No clear swing (5% or more) in the last 6 months for ${name}, so there are no Fibonacci levels to read.</li></ul>`;
      return;
    }
    const [cls, read] = FIB_READS[f.read];
    const up = f.direction === "up";
    const at = (r) => (f.levels.find((l) => l.ratio === r) || {}).price;
    const price = (v) => (v == null ? "—" : `${money(v, digits)} ${unit}`);
    const cell = (k, v, note) => `<div><span class="muted small">${k}</span><b>${v}</b><span class="muted small">${note}</span></div>`;
    const cells = up && f.entry != null
      ? cell("Buy zone (61.8% to 38.2%)", `${money(f.zone.low, digits)} – ${price(f.zone.high)}`, "where rises often resume")
        + cell("Entry", price(f.entry), f.retracePct >= 38.2 ? "the price now: already in the zone or below it" : "a dip to the 50% level")
        + cell("Rethink below", price(f.stop), "the 78.6% level: past it the rise has likely failed")
        + cell("Target", price(f.target), f.target > f.high * 1.001 ? "the 1.272 extension, past the high" : "the swing high")
      : cell("Close above to buy", price(at(0.618)), "the 61.8% level") + cell("Stronger sign", price(at(0.786)), "the 78.6% level")
        + cell("Swing low", price(f.low), up ? "the rise has given back nearly all of it" : "where the fall ended");
    box.innerHTML = `<ul><li class="${cls}"><b>${up ? "Rise" : "Fall"} from ${money(up ? f.low : f.high, digits)} to ${money(up ? f.high : f.low, digits)} ${unit}, `
      + `${up ? "given back" : "taken back"} ${f.retracePct.toFixed(0)}%.</b> ${escapeHtml(read)}</li></ul>
      <div class="fib-grid">${cells}</div>
      <p class="muted small">For ${name}. The swing ends at the highest or lowest price of the last 6 months and starts where that move began (up to a year back); the levels are fixed fractions of it,
        which many traders watch, so prices often pause there. A guide to where to buy and where to give up, not a forecast.</p>`;
  }

  function drawFibLines() {
    for (const l of state.fibLines) state.line.removePriceLine(l);
    const f = fibFor(state.unit, state.data);
    state.fibLines = f ? f.levels.map((l) => state.line.createPriceLine({ price: l.price, color: css("--fib"), lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: `Fib ${fibPct(l.ratio)}` })) : [];
  }

  // ---- Elliott wave: counted on the same three series, scaled to the unit shown like the Fibonacci levels ----
  const EW_READS = { early: "good", done: "good", late: "flat", corrective: "flat", turn: "flat", bearish: "bad", broken: "bad" };

  function elliottFor(unit, d) {
    const e = d.elliott && d.elliott[unit === "usd" || unit === "rate" ? unit : "egp"];
    if (!e) return null;
    const k = unit === "usd" || unit === "rate" ? 1 : inUnit({ gram24: 1 }, unit, d);
    const x = (v) => (v == null ? null : v * k);
    // the server's text quotes the 24k gram: restate its two prices in the unit shown (formatted as app/elliott._fmt does)
    const pyFmt = (v) => v.toLocaleString("en-US", Math.abs(v) < 1000 ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : { maximumFractionDigits: 0 });
    const digits = unit === "rate" ? 2 : 0;
    const text = k === 1 ? e.text : e.text.split(pyFmt(e.target)).join(money(x(e.target), digits)).split(pyFmt(e.invalidation)).join(money(x(e.invalidation), digits));
    return { ...e, text, target: x(e.target), invalidation: x(e.invalidation), labels: e.labels.map((l) => ({ ...l, price: x(l.price) })) };
  }

  const ewName = (l) => (l.label === "new" ? "1" : l.label) + (l.open ? "?" : "");

  function renderElliott() {
    const d = state.data, e = elliottFor(state.unit, d), box = $("elliott");
    const digits = state.unit === "rate" ? 2 : 0, unit = state.unit === "usd" ? "USD" : "EGP";
    if (!e) {
      box.innerHTML = '<ul><li class="flat">No clear Elliott wave count in the last year for the unit shown.</li></ul>';
      return;
    }
    const price = (v) => `${money(v, digits)} ${unit}`;
    const cell = (k, v, note) => `<div><span class="muted small">${k}</span><b>${v}</b><span class="muted small">${note}</span></div>`;
    const path = e.labels.map((l) => `${ewName(l)} ${money(l.price, digits)}`).join(" → ");
    box.innerHTML = `<ul><li class="${EW_READS[e.read] || "flat"}"><b>${e.direction === "up" ? "Rising" : "Falling"} count, now in ${e.wave === "new" ? "a new move" : `wave ${e.wave}`}.</b>
        ${escapeHtml(e.text)}</li></ul>
      <div class="fib-grid">${cell("Usual target", price(e.target), "where this wave often ends")}
        ${cell("Count wrong past", price(e.invalidation), "a rule of the count breaks there")}
        ${cell("Fit", `${Math.round(e.score * 100)}%`, "how closely the swings match the usual proportions")}</div>
      <p class="muted small">Swings: ${escapeHtml(path)}. Elliott counts are subjective and get redrawn as new swings form; read this as where the move
        probably stands, beside the Fibonacci levels, not as a forecast.</p>`;
  }

  function drawElliott() {
    for (const l of state.ewLines) state.line.removePriceLine(l);
    state.ewLines = [];
    const e = elliottFor(state.unit, state.data), color = css("--elliott");
    if (!e) { state.line.setMarkers([]); return; }
    state.line.setMarkers(e.labels.map((l) => ({ time: l.time, position: l.kind === "H" ? "aboveBar" : "belowBar",
      shape: "circle", color, size: 0.1, text: ewName(l) })));
    const { Dashed } = LightweightCharts.LineStyle;
    state.ewLines = [state.line.createPriceLine({ price: e.target, color, lineWidth: 1, lineStyle: Dashed, axisLabelVisible: true, title: "EW target" }),
      state.line.createPriceLine({ price: e.invalidation, color, lineWidth: 1, lineStyle: Dashed, axisLabelVisible: true, title: "EW wrong past" })];
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
    drawFibLines();
    renderFib();
    drawElliott();
    renderElliott();
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
      renderDecision();
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
  for (const f of FIELDS) $(f).addEventListener("input", () => { localCheck(); myGold(); renderDecision(); save(); });
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
