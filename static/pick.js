(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const whole = (v) => (v == null ? "—" : Math.round(v).toLocaleString());
  const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const tone = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
  const chart = (sym) => `/?${new URLSearchParams({ symbol: sym })}`;
  const COUNT_LABEL = { "strong-buy": "Strong buy", buy: "Buy", add: "Add more", wait: "Wait", "dont-buy": "Don't buy", hold: "Hold",
    reduce: "Reduce", sell: "Sell", unknown: "Not enough data", index: "Index" };
  const halalBadge = (h) => (!h ? "" : h.status === "halal"
    ? `<span class="halal${h.source === "mine" ? " mine" : ""}" title="${h.source === "mine" ? "Halal: your own tag" : "In the EGX 33 Shariah list"}">Halal</span>`
    : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  async function loadUniverses() {
    try {
      const u = await (await fetch("/api/universes")).json();
      // stocks only: the routine ends in a buy, and an index can't be bought
      const keys = Object.keys(u).filter((k) => k !== "world");
      const first = ["egx_top", ...keys.filter((k) => k !== "egx_top")].filter((k) => u[k]);
      $("universe").innerHTML = first.map((k) => `<option value="${k}">${escapeHtml(u[k].label)} (${u[k].count})</option>`).join("")
        + '<option value="profile">The stocks in My profile</option>';
    } catch (_) {}
    try {
      const saved = JSON.parse(localStorage.getItem("pick") || "{}");
      for (const k of ["universe", "bars"]) if (saved[k] && [...$(k).options].some((o) => o.value === saved[k])) $(k).value = saved[k];
      $("halalOnly").checked = !!saved.halalOnly;
    } catch (_) {}
  }

  async function run(refresh) {
    const btn = document.querySelector("#run button");
    const params = { universe: $("universe").value, bars: $("bars").value, halal_only: $("halalOnly").checked, refresh: !!refresh };
    try { localStorage.setItem("pick", JSON.stringify({ universe: params.universe, bars: params.bars, halalOnly: params.halal_only })); } catch (_) {}
    btn.disabled = true;
    setStatus("Running the routine… checking every stock in the list takes up to a minute the first time.");
    try {
      const res = await fetch(`/api/routine?${new URLSearchParams(params)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      render(body);
      setStatus("");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  }

  const items = (rows) => `<ul>${rows.map(([cls, html]) => `<li class="${cls}">${html}</li>`).join("")}</ul>`;
  const TONE_LI = { bullish: "good", bearish: "bad", neutral: "flat" };

  function planCard(p, marketGo) {
    const lines = [];
    if (p.shares) {
      lines.push(`<b>Plan:</b> buy <b>${p.shares.toLocaleString()}</b> shares at about <b>${fmt(p.entry)}</b>, stop <b>${fmt(p.stop)}</b> (${pct(p.stopPct)}), `
        + `target <b>${fmt(p.target)}</b> (${pct(p.upsidePct)}); reward is ${p.rr == null ? "—" : p.rr.toFixed(1)} times the risk.`);
      lines.push(`<b>Risk:</b> about ${whole(p.risk)} if the stop is hit (${p.riskPct == null ? "—" : p.riskPct.toFixed(2)}% of your capital); the buy costs about ${whole(p.cost)}.`
        + (p.limitedBy ? ` <span class="muted">${escapeHtml(p.limitedBy)}</span>` : ""));
    } else {
      lines.push(`<b>Levels:</b> entry ${fmt(p.entry)}, stop ${fmt(p.stop)}, target ${fmt(p.target)}; reward is ${p.rr == null ? "—" : p.rr.toFixed(1)} times the risk. No share count: see the flag below.`);
    }
    lines.push(`<span class="muted">Levels are ${p.levelsFrom === "profile" ? "your own, from My profile" : "the chart's suggestion: set your own in My profile if you disagree"}.</span>`);
    if (p.for.length) lines.push(`<b>For:</b> ${escapeHtml(p.for.join("; "))}.`);
    if (p.against.length) lines.push(`<b>Against / watch:</b><ul class="cons">${p.against.map((a) => `<li><b>${escapeHtml(a.label)}.</b> ${escapeHtml(a.detail)}</li>`).join("")}</ul>`);
    if (p.noData.length) lines.push(`<span class="muted"><b>No data for:</b> ${escapeHtml(p.noData.join(", "))}.</span>`);
    const t = p.timing;
    lines.push(t
      ? `<b>Entry timing:</b> ${t.passed}/5 intraday rules on the bar of ${escapeHtml(t.lastBar)}. ${escapeHtml(t.advice)} `
        + t.checks.map((c) => `<span class="rule ${c.ok == null ? "na" : c.ok ? "ok" : "no"}" title="${escapeHtml(c.text)}">${escapeHtml(c.label)}</span>`).join("")
      : '<b>Entry timing:</b> <span class="muted">no intraday data for this stock.</span>');
    const flags = p.flags.map((f) => `<div class="plan-flag">⚠ ${escapeHtml(f.text)}</div>`).join("");
    return `<div class="call-box plan ${p.clean && marketGo ? "bullish" : ""}">
      <div class="call-head">
        <div><div class="call-sym"><a href="${chart(p.symbol)}" target="_blank" rel="noopener">${escapeHtml(p.symbol)}</a> ${halalBadge(p.halal)}</div>
          <div class="muted">${escapeHtml(p.name)} · ${fmt(p.last)} <span class="${tone(p.changePct)}">${pct(p.changePct)}</span> · last bar ${escapeHtml(p.lastBar || "—")}</div></div>
        <div class="call-verdict"><div class="call-label">${escapeHtml(p.call)}</div>${p.held ? `<div class="muted small">already held (${escapeHtml(p.held)})</div>` : ""}</div>
      </div>
      <p class="call-reason">${escapeHtml(p.reason)}</p>
      ${flags}
      <div class="call-do">${lines.join("<br />")}</div>
      <div class="plan-links"><a href="${chart(p.symbol)}" target="_blank" rel="noopener">Chart and full checklist</a> ·
        <a href="/decide?${new URLSearchParams({ symbol: p.symbol })}" target="_blank" rel="noopener">Every input behind the call</a> ·
        <a href="/profile" target="_blank" rel="noopener">Add in My profile</a></div>
    </div>`;
  }

  function render(d) {
    $("result").hidden = false;
    $("meta").textContent = d.asOf ? `checked ${new Date(d.asOf * 1000).toLocaleString()}` : "";
    $("outcome").className = `call-box ${d.outcome.tone}`;
    $("outcome").innerHTML = `<div class="call-label">${escapeHtml(d.outcome.title)}</div><p class="call-reason">${escapeHtml(d.outcome.text)}</p>
      <div class="plan-links"><a href="#" id="again">Check the list again now</a> <span class="muted">(results are kept for 15 minutes)</span></div>`;
    $("again").addEventListener("click", (e) => { e.preventDefault(); run(true); });

    const m = d.market;
    $("market").innerHTML = items([[TONE_LI[m.tone], `<b>${escapeHtml(m.label)}.</b> ${escapeHtml(m.text)} ${escapeHtml(m.advice)}`]])
      + '<p class="muted small">From EGX 30\'s trend and how many EGX stocks are above their 200-day average. Details on the <a href="/market">Market</a> page.</p>';

    const s = d.sectors;
    const chips = (xs) => xs.map((x) => `<a class="sector-chip" href="/screener?universe=egx_sector_${encodeURIComponent(x.slug)}" title="${escapeHtml(x.label)}: open its stocks in the screener">${escapeHtml(x.sector)}</a>`).join(" ");
    $("sectors").innerHTML = !s ? items([["flat", "The sector outlook didn't load, so sectors aren't weighed here."]])
      : items([
        [s.prefer.length ? "good" : "flat", s.prefer.length ? `<b>Prefer:</b> ${chips(s.prefer)}` : "<b>Prefer:</b> no sector has both good financials and a supportive trend right now."],
        ["bad", s.wary.length ? `<b>Be wary of:</b> ${chips(s.wary)}` : "<b>Be wary of:</b> none flagged."],
      ]) + '<p class="muted small">Financials crossed with price trend, per sector. Details on the <a href="/sectors">Sectors</a> page.</p>';

    const sh = d.shortlist;
    const counts = Object.entries(sh.counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v} ${COUNT_LABEL[k] || k}`).join(", ");
    $("shortNote").textContent = `${sh.checked} stocks checked`;
    $("shortlist").innerHTML = items([[sh.buys ? "good" : "flat",
      `<b>${sh.buys} buy call${sh.buys === 1 ? "" : "s"}</b> out of ${sh.checked}: ${escapeHtml(counts)}.`
      + (sh.buys > sh.shown ? ` Showing the top ${sh.shown}${$("halalOnly").checked ? " that are halal" : ""}.` : "")]])
      + (sh.skipped.length ? `<p class="muted small">No data for: ${escapeHtml(sh.skipped.join(", "))}.</p>` : "");

    $("plansHead").hidden = $("plans").hidden = !d.plans.length;
    $("plans").innerHTML = (d.market.go ? "" : '<p class="hint">The market is weak, so these are held back: they would qualify on their own, but most stocks fall with a falling market.</p>')
      + d.plans.map((p) => planCard(p, d.market.go)).join("");

    $("watch").hidden = !d.watch.length;
    $("watchNone").hidden = !!d.watch.length;
    $("watch").querySelector("tbody").innerHTML = d.watch.map((w) => `<tr data-symbol="${escapeHtml(w.symbol)}">
        <td><div class="sym">${escapeHtml(w.symbol)} ${halalBadge(w.halal)}</div><div class="muted small">${escapeHtml(w.name)}</div></td>
        <td class="num">${fmt(w.last)} <span class="${tone(w.changePct)} small">${pct(w.changePct)}</span></td>
        <td class="num">${w.passed}/${w.known}</td>
        <td>${escapeHtml(w.reason)}</td>
      </tr>`).join("");
    $("note").textContent = d.note;
  }

  $("run").addEventListener("submit", (e) => { e.preventDefault(); run(false); });
  document.querySelector("#watch tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    if (tr) window.open(chart(tr.dataset.symbol), "_blank", "noopener");
  });

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  loadUniverses().then(() => run(false));
})();
