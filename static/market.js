(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const cls = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const compact = (v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${Math.round(v)}`);
  const chart = (sym) => `/?${new URLSearchParams({ symbol: sym, interval: "1d", period: "1y" })}`;

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  // Tiny line chart of the last closes.
  function spark(values, up) {
    if (!values || values.length < 2) return "";
    const w = 160, h = 40, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
    return `<svg class="spark ${up ? "up" : "down"}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" /></svg>`;
  }

  function renderIndices(list) {
    $("indices").innerHTML = list.map((i) => i.error
      ? `<div class="index-card"><div class="muted">${escapeHtml(i.name)}: no data</div></div>`
      : `<a class="index-card" href="${chart(i.symbol)}">
          <div class="ic-head"><b>${escapeHtml(i.name)}</b><span class="trend ${i.trend.toLowerCase()}">${escapeHtml(i.trend)}</span></div>
          <div class="ic-price"><span class="big">${fmt(i.last)}</span> <span class="${cls(i.day)}">${pct(i.day)}</span></div>
          ${spark(i.spark, i.month >= 0)}
          <div class="ic-stats">
            <span>Week <b class="${cls(i.week)}">${pct(i.week)}</b></span>
            <span>Month <b class="${cls(i.month)}">${pct(i.month)}</b></span>
            <span>This year <b class="${cls(i.ytd)}">${pct(i.ytd)}</b></span>
          </div>
          <div class="muted small">${i.above200 == null ? "" : i.above200 ? "Above" : "Below"} its 200-day average · ${escapeHtml(i.date)}</div>
        </a>`).join("");
  }

  function renderBreadth(b) {
    const tot = b.advancers + b.decliners + b.unchanged || 1;
    const mood = b.advancers > b.decliners * 1.5 ? "Broad buying" : b.decliners > b.advancers * 1.5 ? "Broad selling" : "Mixed day";
    $("breadth").innerHTML = `
      <div class="tile">
        <h3>Today · ${mood}</h3>
        <div class="split"><span class="seg bullish" style="flex:${b.advancers}"></span><span class="seg" style="flex:${b.unchanged}"></span><span class="seg bearish" style="flex:${b.decliners}"></span></div>
        <div class="split-legend"><span><i class="sw bullish"></i>Up <b>${b.advancers}</b></span><span><i class="sw"></i>Flat <b>${b.unchanged}</b></span><span><i class="sw bearish"></i>Down <b>${b.decliners}</b></span></div>
        <div class="muted small">${b.traded} stocks traded · ${Math.round((b.advancers / tot) * 100)}% up</div>
      </div>
      <div class="tile"><h3>Above 50-day average</h3><div class="big">${b.above50 ?? "—"}%</div><div class="muted small">short-term trend: share of stocks above their 50-day average</div></div>
      <div class="tile"><h3>Above 200-day average</h3><div class="big">${b.above200 ?? "—"}%</div><div class="muted small">long-term trend: above 60% is a healthy market, below 40% a weak one</div></div>
      <div class="tile"><h3>Value traded</h3><div class="big">${compact(b.value)}</div><div class="muted small">EGP, latest session</div></div>`;
  }

  function renderSectors(rows) {
    const max = Math.max(...rows.map((r) => Math.abs(r.changePct)), 0.5);
    $("sectors").innerHTML = rows.map((r) => {
      const a = Math.round((Math.min(Math.abs(r.changePct) / max, 1) * 0.55 + 0.08) * 100);
      const bg = `color-mix(in srgb, var(${r.changePct >= 0 ? "--bull" : "--bear"}) ${a}%, transparent)`;
      const u = new URLSearchParams({ universe: `egx_sector_${r.slug}` });
      return `<a class="sector-tile" style="background:${bg}" href="/screener?${u}" title="${r.adv} up, ${r.dec} down · ${compact(r.value)} EGP traded">
        <b>${escapeHtml(r.sector)}</b><span class="sc">${pct(r.changePct)}</span><span class="muted small">${r.count} stocks · ${compact(r.value)}</span></a>`;
    }).join("");
  }

  function renderMovers(id, rows) {
    $(id).querySelector("tbody").innerHTML = rows.map((s) => `
      <tr data-symbol="${escapeHtml(s.symbol)}">
        <td><div class="sym">${escapeHtml(s.symbol.replace(".CA", ""))}</div><div class="muted small">${escapeHtml(s.name)}</div></td>
        <td class="num">${fmt(s.close)}</td>
        <td class="num ${cls(s.changePct)}">${pct(s.changePct)}</td>
        <td class="num muted">${compact(s.value)}</td>
      </tr>`).join("") || '<tr><td class="muted">None</td></tr>';
  }

  async function load() {
    $("refresh").disabled = true;
    try {
      const res = await fetch("/api/market");
      const b = await res.json();
      if (!res.ok) throw new Error(b.detail || `Request failed (${res.status})`);
      $("asOf").textContent = `Snapshot ${new Date(b.asOf * 1000).toLocaleString()} (delayed)`;
      renderIndices(b.indices);
      renderBreadth(b.breadth);
      renderSectors(b.sectors);
      renderMovers("gainers", b.gainers);
      renderMovers("losers", b.losers);
      renderMovers("active", b.active);
      $("content").hidden = false;
      setStatus("");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      $("refresh").disabled = false;
    }
  }

  document.querySelector(".movers").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    if (tr) location.href = chart(tr.dataset.symbol);
  });
  $("refresh").addEventListener("click", load);

  const root = document.documentElement;
  const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

  // TradingView's embeddable stock heatmap for the whole Egyptian market; rebuilt when the theme flips.
  function renderHeatmap() {
    const cfg = {
      dataSource: "AllEG", grouping: "sector", blockSize: "market_cap_basic", blockColor: "change",
      colorTheme: isDark() ? "dark" : "light", hasTopBar: false, isDataSetEnabled: false, isZoomEnabled: true,
      hasSymbolTooltip: true, isMonoSize: false, symbolUrl: "", autosize: true,
    };
    $("heatmap").innerHTML = `<iframe title="EGX stock heatmap" loading="lazy" src="https://s.tradingview.com/embed-widget/stock-heatmap/?locale=en#${encodeURIComponent(JSON.stringify(cfg))}"></iframe>`;
  }

  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    root.dataset.theme = isDark() ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
    renderHeatmap();
  });

  renderHeatmap();
  load();
})();
