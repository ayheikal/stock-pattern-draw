(() => {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], sortKey: null, sortDir: 1, interval: "1d" };
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const compact = (v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${Math.round(v)}`);
  const pct = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;

  function setStatus(msg, isError = false) {
    const el = $("scanStatus");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  async function loadUniverses() {
    const res = await fetch("/api/universes");
    const u = await res.json();
    $("universe").innerHTML = Object.entries(u).map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)} (${v.count})</option>`).join("");
    try {
      const saved = JSON.parse(localStorage.getItem("screener") || "{}");
      for (const k of ["universe", "symbols", "minScore", "minAdtv", "interval"]) if (saved[k] != null) $(k).value = saved[k];
    } catch (_) {}
  }

  async function scan() {
    const btn = document.querySelector("#scan button");
    const params = { universe: $("universe").value, symbols: $("symbols").value.trim(), min_score: $("minScore").value, min_adtv: $("minAdtv").value, interval: $("interval").value, period: $("interval").value === "1wk" ? "5y" : "1y" };
    try { localStorage.setItem("screener", JSON.stringify({ universe: params.universe, symbols: params.symbols, minScore: params.min_score, minAdtv: params.min_adtv, interval: params.interval })); } catch (_) {}
    btn.disabled = true;
    setStatus("Scanning… this can take a few seconds per dozen symbols.");
    $("results").hidden = true;
    const t0 = performance.now();
    try {
      const res = await fetch(`/api/screen?${new URLSearchParams(params)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      state.rows = body.results;
      state.interval = params.interval;
      state.sortKey = null;
      $("found").textContent = body.results.length;
      $("scanInfo").textContent = `${body.scanned} scanned in ${((performance.now() - t0) / 1000).toFixed(1)}s`
        + (body.skipped.length ? ` · skipped (no data): ${body.skipped.join(", ")}` : "");
      renderTable();
      setStatus(body.results.length ? "" : "No symbol in this list has a bullish outlook right now.");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  }

  function renderTable() {
    const rows = [...state.rows];
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
    $("results").querySelector("tbody").innerHTML = rows.map((r) => `
      <tr data-symbol="${escapeHtml(r.symbol)}">
        <td><div class="sym">${escapeHtml(r.symbol)}</div><div class="muted small">${escapeHtml(r.name)}</div></td>
        <td class="num">${fmt(r.last)}</td>
        <td class="num ${r.changePct >= 0 ? "up" : "down"}">${pct(r.changePct)}</td>
        <td class="num"><span class="bias ${/bullish/i.test(r.bias) ? "bullish" : ""}">${escapeHtml(r.bias)}</span> <span class="muted small">${r.score.toFixed(2)}</span></td>
        <td class="num up">${pct(r.upsidePct)}</td>
        <td class="num">${fmt(r.upTarget)}</td>
        <td class="num">${fmt(r.stop)}</td>
        <td class="num">${r.rr != null ? r.rr.toFixed(2) : "—"}</td>
        <td>${r.aboveSma200 == null ? "—" : r.aboveSma200 ? '<span class="up">Above</span>' : '<span class="down">Below</span>'}</td>
        <td><span class="action ${/buy/i.test(r.action) ? "good" : "caution"}">${escapeHtml(r.action)}</span></td>
        <td class="num">${r.adtv == null ? "—" : compact(r.adtv)}</td>
        <td class="signal">${escapeHtml(r.signal)}</td>
      </tr>`).join("");
    $("results").hidden = !rows.length;
  }

  $("scan").addEventListener("submit", (e) => { e.preventDefault(); scan(); });
  document.querySelector("#results thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    const k = th.dataset.k;
    state.sortDir = state.sortKey === k ? -state.sortDir : (["symbol", "action", "signal"].includes(k) ? 1 : -1);
    state.sortKey = k;
    renderTable();
  });
  document.querySelector("#results tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    if (tr) location.href = `/?${new URLSearchParams({ symbol: tr.dataset.symbol, interval: state.interval, period: state.interval === "1wk" ? "5y" : "1y" })}`;
  });

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  // A link like /screener?universe=egx_sector_finance (from the Market page) opens that list and scans it.
  loadUniverses().then(() => {
    const u = new URLSearchParams(location.search).get("universe");
    if (u && [...$("universe").options].some((o) => o.value === u)) {
      $("universe").value = u;
      $("symbols").value = "";
      scan();
    }
  });
})();
