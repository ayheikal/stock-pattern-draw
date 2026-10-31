(() => {
  const $ = (id) => document.getElementById(id);
  const state = { body: null, sortKey: null, sortDir: 1 };
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const compact = (v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${Math.round(v)}`);
  const halalBadge = (h) => (!h ? "" : h.status === "halal"
    ? `<span class="halal${h.source === "mine" ? " mine" : ""}" title="${h.source === "mine" ? "Halal: your own tag" : "In the EGX 33 Shariah list"}">Halal</span>`
    : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');
  // Verdicts reuse the buy checklist's colours
  const PILL = { buy: "ready", watch: "wait", unknown: "unknown", trap: "avoid" };
  const PICK_PILL = { short: "wait", dear: "avoid" }; // a looked-up stock needn't be cheap
  const LABEL = { buy: "Buy zone", watch: "Watch for a turn", unknown: "Check the figures", trap: "Cheap for a reason" };
  const HINT = { buy: "cheap, sound, rising trend", watch: "cheap and sound, still falling", unknown: "too few figures to grade", trap: "losing money or weak" };
  const SHOW = { buy: ["buy"], sound: ["buy", "watch"], known: ["buy", "watch", "unknown"], all: null };
  const FILTERS = ["margin", "show", "minValue", "sector"];

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  function save() {
    const s = Object.fromEntries(FILTERS.map((k) => [k, $(k).value]));
    try { localStorage.setItem("value", JSON.stringify({ ...s, halalOnly: $("halalOnly").checked })); } catch (_) {}
  }

  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem("value") || "{}");
      for (const k of FILTERS) if (saved[k] != null) $(k).dataset.saved = saved[k];
      for (const k of ["margin", "show", "minValue"]) if (saved[k] != null) $(k).value = saved[k];
      $("halalOnly").checked = !!saved.halalOnly;
    } catch (_) {}
  }

  async function load() {
    $("refresh").disabled = true;
    setStatus("Loading company financials…");
    try {
      const res = await fetch(`/api/value?${new URLSearchParams({ margin: $("margin").value })}`);
      const b = await res.json();
      if (!res.ok) throw new Error(typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`);
      state.body = b;
      $("asOf").textContent = `${b.valued} of ${b.total} stocks valued · financials as of ${new Date(b.asOf * 1000).toLocaleString()}`;
      const sectors = [...new Set(b.results.map((r) => r.sector).filter(Boolean))].sort();
      const want = $("sector").value || $("sector").dataset.saved || "";
      $("sector").innerHTML = '<option value="">All sectors</option>' + sectors.map((s) => `<option${s === want ? " selected" : ""}>${escapeHtml(s)}</option>`).join("");
      $("tickers").innerHTML = (b.tickers || []).map(([s, name]) => `<option value="${escapeHtml(s.replace(".CA", ""))}">${escapeHtml(name || "")}</option>`).join("");
      render();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      $("refresh").disabled = false;
    }
  }

  function filtered() {
    const b = state.body, keep = SHOW[$("show").value], min = Number($("minValue").value), sector = $("sector").value;
    return b.results.filter((r) => (!keep || keep.includes(r.verdict)) && (r.value || 0) >= min && (!sector || r.sector === sector)
      && (!$("halalOnly").checked || (r.halal || {}).status === "halal"));
  }

  function renderSummary(rows) {
    $("summary").innerHTML = Object.keys(PILL).map((k) => {
      const n = rows.filter((r) => r.verdict === k).length;
      const all = state.body.results.filter((r) => r.verdict === k).length;
      return `<div class="tile"><h3><span class="verdict ${PILL[k]}">${LABEL[k]}</span></h3>
        <div class="big">${n}</div><div class="muted small">${HINT[k]}${all !== n ? ` · ${all - n} hidden by filters` : ""}</div></div>`;
    }).join("");
    $("summary").hidden = false;
  }

  const methodsCell = (r) => r.methods.map((m) => `<div title="${escapeHtml(`${m.label} ${m.value} vs ${m.peerMedian} median of ${m.peers} → fair ${fmt(m.fair)}`)}">`
    + `${m.label} <b>${m.value}</b> <span class="muted">vs ${m.peerMedian}</span> → ${fmt(m.fair)}</div>`).join("");

  function fundCell(r) {
    const cls = r.grade === "strong" ? "up" : r.grade === "weak" ? "down" : "";
    const flags = r.flags.length ? `<div class="muted small" title="${escapeHtml(r.flags.join(" · "))}">${r.flags.length} red flag${r.flags.length > 1 ? "s" : ""}</div>` : "";
    return `<b class="${cls}">${escapeHtml(r.gradeLabel)}</b>${r.fundPct == null ? "" : ` <span class="muted">${r.fundPct}%</span>`}${flags}`;
  }

  // Only a looked-up stock can be above its fair value (a negative discount)
  const rowHtml = (r) => `
      <tr data-symbol="${escapeHtml(r.symbol)}">
        <td><div class="sym">${escapeHtml(r.symbol.replace(".CA", ""))} ${halalBadge(r.halal)}</div><div class="muted small">${escapeHtml(r.name)}</div>
          <div class="muted small">${escapeHtml(r.industry || r.sector || "")}</div></td>
        <td class="checks-cell" title="${escapeHtml(r.text)}"><span class="verdict ${PILL[r.verdict] || PICK_PILL[r.verdict]}">${escapeHtml(r.label)}</span></td>
        <td class="num">${fmt(r.price)}</td>
        <td class="num"><b>${fmt(r.fair)}</b></td>
        <td class="num ${r.discountPct >= 0 ? "up" : "down"}">${r.discountPct.toFixed(0)}%</td>
        <td class="num ${r.upsidePct >= 0 ? "up" : "down"}">${r.upsidePct >= 0 ? "+" : ""}${r.upsidePct.toFixed(0)}%</td>
        <td class="small nowrap">${methodsCell(r)}</td>
        <td class="num">${fundCell(r)}</td>
        <td>${r.above200 == null ? '<span class="muted">New</span>' : r.above200 ? '<span class="up">Above 200d</span>' : '<span class="down">Below 200d</span>'}</td>
        <td class="num">${r.value ? compact(r.value) : "—"}</td>
        <td class="num">${r.dividendYield ? `${r.dividendYield.toFixed(1)}%` : "—"}</td>
        <td class="nowrap">${r.nextEarnings ? escapeHtml(r.nextEarnings) : '<span class="muted">—</span>'}</td>
      </tr>`;

  // ---------- Ticker lookup: any EGX stock, cheap or not, whatever the filters ----------
  let picked = "";

  function setPickUrl(sym) {
    const u = new URL(location.href);
    if (sym) u.searchParams.set("symbol", sym); else u.searchParams.delete("symbol");
    history.replaceState(null, "", u);
  }

  async function pick(force = false) {
    const sym = $("ticker").value.trim().toUpperCase();
    const key = `${sym}|${$("margin").value}`;
    if (!force && key === picked) return; // Enter fires both change and submit
    picked = key;
    setPickUrl(sym);
    if (!sym) { $("pick").hidden = true; return; }
    $("pick").hidden = false;
    $("picked").hidden = true;
    $("pickText").textContent = "";
    $("pickStatus").classList.remove("error");
    $("pickStatus").hidden = false;
    $("pickStatus").textContent = `Valuing ${sym}…`;
    try {
      const res = await fetch(`/api/value/stock?${new URLSearchParams({ symbol: sym, margin: $("margin").value })}`);
      const b = await res.json();
      if (!res.ok) throw new Error(typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`);
      if (picked !== key) return; // another ticker was picked meanwhile
      if (!b.stock) {
        $("pickStatus").textContent = b.reason;
        return;
      }
      $("pickStatus").hidden = true;
      $("pickText").textContent = b.stock.text;
      $("picked").querySelector("thead").innerHTML = $("results").querySelector("thead").innerHTML.replace(/ data-(k|dir)="[^"]*"/g, "").replace(/\bsorted\b/g, "");
      $("picked").querySelector("tbody").innerHTML = rowHtml(b.stock);
      $("picked").hidden = false;
    } catch (err) {
      if (picked !== key) return;
      $("pickStatus").textContent = err.message;
      $("pickStatus").classList.add("error");
    }
  }

  function render() {
    const rows = filtered();
    if (state.sortKey) {
      const k = state.sortKey;
      rows.sort((a, b) => {
        const x = a[k], y = b[k];
        if (x == null) return 1;
        if (y == null) return -1;
        return (typeof x === "string" ? x.localeCompare(y) : x - y) * state.sortDir;
      });
    }
    document.querySelectorAll("#results th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.k === state.sortKey);
      th.dataset.dir = th.dataset.k === state.sortKey ? (state.sortDir > 0 ? "▲" : "▼") : "";
    });
    renderSummary(rows);
    $("results").querySelector("tbody").innerHTML = rows.map(rowHtml).join("");
    $("results").hidden = !rows.length;
    $("found").textContent = rows.length;
    $("found").hidden = false;
    setStatus(rows.length ? "" : `Nothing ${$("margin").value}%+ below fair value matches these filters. Try a smaller margin, <b>Show: Everything cheap</b> or a looser <b>Liquidity</b>.`);
  }

  $("filters").addEventListener("change", (e) => {
    if (e.target.id === "ticker") { pick(); return; }
    save();
    if (e.target.id === "margin") { load(); if ($("ticker").value.trim()) pick(); } // the verdict depends on the margin
    else if (state.body) render();
  });
  $("filters").addEventListener("submit", (e) => { e.preventDefault(); pick(); });
  $("pickClear").addEventListener("click", () => { $("ticker").value = ""; pick(); $("ticker").focus(); });
  $("refresh").addEventListener("click", () => { load(); if ($("ticker").value.trim()) pick(true); });
  document.querySelector("#results thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    const k = th.dataset.k;
    state.sortDir = state.sortKey === k ? -state.sortDir : (["symbol", "rank", "nextEarnings"].includes(k) ? 1 : -1);
    state.sortKey = k;
    render();
  });
  document.querySelectorAll("#results tbody, #picked tbody").forEach((tbody) => tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    // A new tab keeps this list here to come back to; the chart page has the full buy checklist.
    if (tr) window.open(`/?${new URLSearchParams({ symbol: tr.dataset.symbol, interval: "1d", period: "1y" })}`, "_blank", "noopener");
  }));

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  restore();
  load();
  const linked = new URLSearchParams(location.search).get("symbol"); // deep link: /value?symbol=COMI
  if (linked) { $("ticker").value = linked; pick(); }
})();
