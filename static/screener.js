(() => {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], sortKey: null, sortDir: 1, interval: "1d" };
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const compact = (v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${Math.round(v)}`);
  const pct = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
  const halalBadge = (h) => (!h ? "" : h.status === "halal"
    ? `<span class="halal${h.source === "mine" ? " mine" : ""}" title="${h.source === "mine" ? "Halal: your own tag" : "In the EGX 33 Shariah list"}">Halal</span>`
    : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');

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
      for (const k of ["universe", "symbols", "show", "minAdtv", "interval"]) if (saved[k] != null) $(k).value = saved[k];
      $("halalOnly").checked = !!saved.halalOnly;
    } catch (_) {}
    syncWatchlist();
  }

  // Typed symbols override the watchlist, so grey it out while they're there.
  function syncWatchlist() {
    const own = !!$("symbols").value.trim();
    $("universe").disabled = own;
    $("universe").title = own ? "Ignored while your own symbols are filled in" : "";
  }

  async function scan() {
    const btn = document.querySelector("#scan button");
    const params = { universe: $("universe").value, symbols: $("symbols").value.trim(), show: $("show").value, min_adtv: $("minAdtv").value, interval: $("interval").value, period: $("interval").value === "1wk" ? "5y" : "1y", halal_only: $("halalOnly").checked };
    try { localStorage.setItem("screener", JSON.stringify({ universe: params.universe, symbols: params.symbols, show: params.show, minAdtv: params.min_adtv, interval: params.interval, halalOnly: params.halal_only })); } catch (_) {}
    btn.disabled = true;
    setStatus("Scanning… this can take a few seconds per dozen symbols.");
    $("results").hidden = true;
    const t0 = performance.now();
    try {
      const res = await fetch(`/api/screen?${new URLSearchParams(params)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      // Target / stop / R:R are the levels the checklist judged (yours from My profile, else the chart's), from today's price.
      state.rows = body.results.map((r) => {
        const p = r.decision.plan;
        const levels = p ? { upTarget: p.target, upsidePct: p.upsidePct, stop: p.stop, rr: p.rr } : { stop: null, rr: null };
        const s = r.decision.rating && r.decision.rating.signal;
        return { ...r, ...levels, verdictRank: r.decision.rank * 100 - (r.decision.passed || 0),
                 ratingRank: s ? s.rank * 1000 - (s.score || 0) : null };
      });
      state.interval = params.interval;
      state.sortKey = null;
      $("found").textContent = body.results.length;
      $("found").hidden = false;
      $("scanInfo").textContent = `${body.scanned} scanned in ${((performance.now() - t0) / 1000).toFixed(1)}s`
        + (body.skipped.length ? ` · skipped (no data): ${body.skipped.join(", ")}` : "");
      renderTable();
      const h = body.hidden || {};
      const hidden = [h.avoid ? `${h.avoid} marked Avoid` : "", h.other ? `${h.other} not Ready` : ""].filter(Boolean).join(" and ");
      setStatus(body.results.length
        ? (hidden ? `${hidden} hidden by <b>Show</b>.` : "")
        : `Nothing to show${hidden ? `: ${hidden}` : ""}. Try <b>Show: Everything</b> to see why, a looser <b>Liquidity</b> setting, or another watchlist.`
          + (params.halal_only ? " <b>Halal only</b> needs the EGX 33 Shariah list in <a href=\"/profile\">My profile</a>, or stocks you tagged halal." : ""));
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  }

  function ratingCell(rt) {
    if (!rt) return '<span class="muted">—</span>';
    const f = rt.fundamental, t = rt.technical || {};
    return `<span class="rsig ${rt.signal.key}" title="${escapeHtml(rt.signal.text)}">${escapeHtml(rt.signal.label)}</span>
      <div class="muted">F ${f.pct == null ? "n/a" : `${f.pct}%`} · ${escapeHtml(t.label || "No trend")}</div>`;
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
        <td><div class="sym">${escapeHtml(r.symbol)} ${halalBadge(r.halal)}</div><div class="muted small">${escapeHtml(r.name)}</div></td>
        <td class="checks-cell" title="${escapeHtml(r.decision.reason)}"><span class="verdict ${r.decision.verdict}">${escapeHtml(r.decision.label)}</span>
          ${r.decision.applicable ? `<div class="muted">${r.decision.passed}/${r.decision.known} pass</div>` : ""}</td>
        <td class="rating-cell">${ratingCell(r.decision.rating)}</td>
        <td class="num">${fmt(r.last)}</td>
        <td class="num ${r.changePct >= 0 ? "up" : "down"}">${pct(r.changePct)}</td>
        <td class="num ${r.upsidePct >= 0 ? "up" : "down"}">${pct(r.upsidePct)}</td>
        <td class="num">${fmt(r.upTarget)}</td>
        <td class="num">${fmt(r.stop)}</td>
        <td class="num">${r.rr != null ? r.rr.toFixed(2) : "—"}</td>
        <td>${r.aboveSma200 == null ? "—" : r.aboveSma200 ? '<span class="up">Above</span>' : '<span class="down">Below</span>'}</td>
        <td class="num">${r.adtv == null ? "—" : compact(r.adtv)}</td>
        <td class="nowrap">${r.nextEarnings ? escapeHtml(r.nextEarnings) : '<span class="muted">—</span>'}</td>
        <td class="num"><span class="bias ${/bullish/i.test(r.bias) ? "bullish" : /bearish/i.test(r.bias) ? "bearish" : ""}">${escapeHtml(r.bias)}</span> <span class="muted small">${r.score.toFixed(2)}</span></td>
        <td class="signal">${escapeHtml(r.signal)}</td>
      </tr>`).join("");
    $("results").hidden = !rows.length;
  }

  $("scan").addEventListener("submit", (e) => { e.preventDefault(); scan(); });
  $("symbols").addEventListener("input", syncWatchlist);
  document.querySelector("#results thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    const k = th.dataset.k;
    state.sortDir = state.sortKey === k ? -state.sortDir : (["symbol", "verdictRank", "ratingRank", "nextEarnings", "signal"].includes(k) ? 1 : -1);
    state.sortKey = k;
    renderTable();
  });
  document.querySelector("#results tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    // A new tab keeps the scan results here to come back to.
    if (tr) window.open(`/?${new URLSearchParams({ symbol: tr.dataset.symbol, interval: state.interval, period: state.interval === "1wk" ? "5y" : "1y" })}`, "_blank", "noopener");
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
      syncWatchlist();
      scan();
    }
  });
})();
