(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, digits = 0) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
  const num = (v, digits = 1) => (v == null ? "—" : Number(v).toFixed(digits));
  const cls = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const compact = (v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${Math.round(v)}`);
  const chart = (sym) => `/?${new URLSearchParams({ symbol: sym, interval: "1d", period: "1y" })}`;
  const LABEL = { growth: "Growth", normal: "Normal", shallow: "Shallow", unknown: "Not enough data" };
  const TONE = { growth: "bullish", shallow: "bearish", normal: "", unknown: "" };
  const HINT = {
    growth: "real revenue and earnings growth, most companies beating inflation",
    normal: "growing about as fast as prices, or mixed",
    shallow: "shrinking in real terms, most companies' earnings lagging inflation",
  };
  // Conclusion verdicts (financials + trend), best first, for the summary tiles
  const VERDICT = {
    favour: { label: "Favour", tone: "bullish", hint: "good financials and a rising trend" },
    accumulate: { label: "Accumulate", tone: "bullish", hint: "one side clearly positive, the other not against it" },
    watch: { label: "Watch", tone: "neutral", hint: "financials and trend disagree, or only one is known" },
    neutral: { label: "Neutral", tone: "neutral", hint: "no edge either way" },
    caution: { label: "Caution", tone: "bearish", hint: "weak financials or a falling trend" },
    avoid: { label: "Avoid", tone: "bearish", hint: "weak financials and a falling trend" },
  };
  let data = null;
  let openSet = new Set();  // sector cards shown expanded; kept between visits
  try { openSet = new Set(JSON.parse(localStorage.getItem("sectorsOpen") || "[]")); } catch (_) {}
  const saveOpen = () => { try { localStorage.setItem("sectorsOpen", JSON.stringify([...openSet])); } catch (_) {} };

  function setOpen(article, open) {
    article.classList.toggle("collapsed", !open);
    article.querySelector(".chev").setAttribute("aria-expanded", String(open));
    article.querySelector(".card-head").title = open ? "Hide details" : "Show details";
    if (open) openSet.add(article.dataset.slug); else openSet.delete(article.dataset.slug);
  }

  const setAll = (open) => {
    document.querySelectorAll(".sector-card").forEach((c) => setOpen(c, open));
    saveOpen();
  };

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  async function api(url, opts) {
    const res = await fetch(url, opts);
    const b = await res.json();
    if (!res.ok) throw new Error(typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`);
    return b;
  }

  function renderSummary(b) {
    const c = b.counts;
    const financial = `<div class="tile"><h3>Financials</h3>${["growth", "normal", "shallow", "unknown"].filter((k) => c[k]).map((k) =>
      `<div class="kv" title="${escapeHtml(HINT[k] || "too few reported figures")}"><span class="status-pill ${k}">${LABEL[k]}</span><b>${c[k]}</b></div>`).join("")}</div>`;
    const verdicts = Object.entries(VERDICT).filter(([k]) => b.verdicts[k]).map(([k, v]) => `
      <div class="tile"><h3><span class="verdict-pill ${v.tone}">${v.label}</span></h3>
        <div class="big">${b.verdicts[k]}</div><div class="muted small">${v.hint}</div></div>`).join("");
    $("summary").innerHTML = verdicts + financial;
  }

  const kv = (label, value, title = "") => `<div class="kv"${title ? ` title="${escapeHtml(title)}"` : ""}><span>${label}</span><span>${value}</span></div>`;

  function eventRow(e) {
    const label = e.symbol ? `<a href="${chart(e.symbol)}">${escapeHtml(e.title)}</a>` : escapeHtml(e.title);
    return `<li class="${e.impact}"><span class="ev-date">${escapeHtml(e.date)}</span> ${label}${e.source === "earnings" ? ' <span class="muted small">earnings date</span>' : ""}</li>`;
  }

  function editor(r) {
    const evs = (r.myEvents || []).map((e, i) => `
      <tr><td>${escapeHtml(e.date)}</td><td>${escapeHtml(e.title)}</td><td class="${e.impact}">${e.impact}</td>
        <td><button type="button" class="x" data-remove="${i}" title="Remove">×</button></td></tr>`).join("");
    return `
      <details class="sector-edit">
        <summary>My view${r.updated ? ` <span class="muted small">updated ${escapeHtml(r.updated)}</span>` : ""}</summary>
        <form class="sector-form" data-slug="${escapeHtml(r.slug)}">
          <label>Status
            <select name="status">
              <option value="">Use the app's suggestion (${LABEL[r.auto]})</option>
              ${["growth", "normal", "shallow"].map((k) => `<option value="${k}"${r.override && r.status === k ? " selected" : ""}>${LABEL[k]}</option>`).join("")}
            </select></label>
          <label class="wide">Why (financial results, plans, news)
            <textarea name="note" rows="3" maxlength="1000" placeholder="e.g. Rate cuts expected to lower banks' interest margins">${escapeHtml(r.note)}</textarea></label>
          <div class="wide">
            <div class="muted small">Upcoming events that should move the sector</div>
            <table class="trade-list">${evs || '<tr><td class="muted">None yet</td></tr>'}</table>
            <div class="trade-form">
              <input type="date" name="evDate" />
              <input name="evTitle" maxlength="120" placeholder="e.g. CBE rate decision" />
              <select name="evImpact"><option value="positive">positive</option><option value="neutral" selected>neutral</option><option value="negative">negative</option></select>
              <button type="button" data-add>Add event</button>
            </div>
          </div>
          <div class="form-actions"><button type="submit" class="primary">Save</button><span class="muted small saved"></span></div>
        </form>
      </details>`;
  }

  function card(r) {
    const growth = (nominal, real) => (nominal == null ? "—" : `<b class="${cls(nominal)}">${pct(nominal)}</b> <span class="muted small">real <b class="${cls(real)}">${pct(real)}</b></span>`);
    const screen = new URLSearchParams({ universe: `egx_sector_${r.slug}` });
    const upcoming = r.events.length
      ? `<ul class="events">${r.events.slice(0, 8).map(eventRow).join("")}</ul>${r.events.length > 8 ? `<div class="muted small">+${r.events.length - 8} more</div>` : ""}`
      : '<div class="muted small">No upcoming events. Add your own below.</div>';
    const recent = r.recent.length ? `<div class="muted small">Recent: ${r.recent.map((e) => `${escapeHtml(e.date)} ${escapeHtml(e.title)}`).join(" · ")}</div>` : "";
    const open = openSet.has(r.slug);
    const next = r.events[0];
    const c = r.conclusion, t = r.technical;
    const mini = escapeHtml(c.text) + (next ? ` <span class="muted">· next: ${escapeHtml(next.date)} ${escapeHtml(next.title)}</span>` : "");
    const trendCls = { up: "up", down: "down" }[t.trend] || "";
    return `
      <article class="card sector-card ${c.tone === "neutral" ? "" : c.tone} ${open ? "" : "collapsed"}" id="sector-${escapeHtml(r.slug)}" data-slug="${escapeHtml(r.slug)}">
        <header class="card-head" data-act="toggle" title="${open ? "Hide details" : "Show details"}">
          <button type="button" class="chev" data-act="toggle" aria-expanded="${open}" aria-label="Toggle details">▸</button>
          <div class="head-main"><b class="sym">${escapeHtml(r.sector)}</b><div class="muted small">${r.count} companies · ${compact(r.marketCap)} EGP market value</div></div>
          <div class="price">
            <span class="verdict-pill ${c.tone}">${escapeHtml(c.label)}</span>
            <div class="muted small">financials <b class="${TONE[r.status] === "bullish" ? "up" : TONE[r.status] === "bearish" ? "down" : ""}">${LABEL[r.status]}</b>
              · trend <b class="${trendCls}">${escapeHtml(t.trendLabel)}</b></div>
          </div>
        </header>
        <div class="card-mini">${mini}</div>
        <div class="card-details">
        <div class="conclusion ${c.tone}">
          <div><b>Conclusion: ${escapeHtml(c.label)}.</b> ${escapeHtml(c.text)}</div>
          ${c.notes.length ? `<ul>${c.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
          <div class="muted small">Financials <span class="status-pill ${r.status}">${LABEL[r.status]}</span>
            ${r.override ? `(your call; the app suggests ${LABEL[r.auto]})` : "(app suggestion)"}
            + trend <b class="${trendCls}">${escapeHtml(t.trendLabel)}</b></div>
        </div>
        <div class="sector-body">
          <div class="tile">
            <h3>Financials <span class="muted small">median company</span></h3>
            ${kv("Revenue growth", growth(r.revenueGrowth, r.realRevenueGrowth), "Yearly (TTM), else the latest quarter vs the one before")}
            ${kv("Earnings growth", growth(r.epsGrowth, r.realEpsGrowth), "Earnings per share, yearly (TTM), else the latest quarter")}
            ${kv("Companies growing", r.growingPct == null ? "—" : `${r.growingPct}%`, "Share of companies with figures whose earnings grew faster than inflation")}
            ${kv("Return on equity", r.roe == null ? "—" : `${num(r.roe)}%`)}
            ${kv("P/E", num(r.pe))}
            ${kv("Dividend yield", r.dividendYield == null ? "—" : `${num(r.dividendYield)}%`)}
            <div class="muted small coverage">growth figures for ${r.withData} of ${r.count} companies</div>
          </div>
          <div class="tile">
            <h3>Technical <span class="muted small">price trend</span></h3>
            ${kv("Trend", `<b class="${trendCls}">${escapeHtml(t.trendLabel)}</b>`)}
            ${kv("Above 50-day average", t.above50 == null ? "—" : `${t.above50}%`, "Share of the sector's stocks closing above their 50-day average (short-term trend)")}
            ${kv("Above 200-day average", t.above200 == null ? "—" : `${t.above200}%`, "Share closing above their 200-day average (long-term trend)")}
            ${kv("1 month", `<b class="${cls(t.perf1m)}">${pct(t.perf1m, 1)}</b>`, "Price change, weighted by company size")}
            ${kv("3 months", `<b class="${cls(t.perf3m)}">${pct(t.perf3m, 1)}</b>`, "Price change, weighted by company size")}
            ${kv("1 year", `<b class="${cls(r.perf1y)}">${pct(r.perf1y, 1)}</b>`)}
            ${kv("RSI (median)", t.rsi == null ? "—" : `${num(t.rsi, 0)}${t.rsi >= 70 ? " overbought" : t.rsi <= 30 ? " oversold" : ""}`, "Above 70: overbought, below 30: oversold")}
          </div>
          <div class="sector-why">
            <h3>Why</h3>
            <ul class="drivers">${r.reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}${r.note ? `<li class="mine"><b>My view:</b> ${escapeHtml(r.note)}</li>` : ""}</ul>
            <h3>Coming up</h3>
            ${upcoming}
            ${recent}
          </div>
        </div>
        <div class="sector-top muted small">
          Largest: ${r.top.map((s) => `<a href="${chart(s.symbol)}" title="${escapeHtml(s.name)} · revenue ${pct(s.revenueGrowth)} · EPS ${pct(s.epsGrowth)}">${escapeHtml(s.symbol.replace(".CA", ""))}</a>`).join(", ")}          · <a href="/screener?${screen}">Screen this sector</a>
        </div>
        ${editor(r)}
        </div>
      </article>`;
  }

  function render(b) {
    data = b;
    $("asOf").textContent = `Financials as of ${new Date(b.asOf * 1000).toLocaleString()}`;
    $("inflation").value = b.inflationPct;
    renderSummary(b);
    const open = new Set([...document.querySelectorAll(".sector-edit[open]")].map((d) => d.closest(".sector-card").id));
    $("cards").innerHTML = b.sectors.map(card).join("");
    open.forEach((id) => { const el = document.querySelector(`#${CSS.escape(id)} .sector-edit`); if (el) el.open = true; });
    $("content").hidden = false;
  }

  async function load() {
    $("refresh").disabled = true;
    try {
      render(await api("/api/sectors"));
      setStatus("");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      $("refresh").disabled = false;
    }
  }

  const rowFor = (slug) => data.sectors.find((r) => r.slug === slug);

  async function saveView(slug, view, savedEl) {
    try {
      render(await api(`/api/sectors/${encodeURIComponent(slug)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(view),
      }));
      const el = document.querySelector(`#sector-${CSS.escape(slug)} .saved`);
      if (el) el.textContent = "Saved";
    } catch (err) {
      if (savedEl) savedEl.textContent = err.message;
    }
  }

  const viewOf = (form, events) => ({
    status: form.status.value || null,
    note: form.note.value,
    events,
  });

  $("cards").addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-act=toggle]");
    if (toggle && !e.target.closest("a")) {
      const article = toggle.closest(".sector-card");
      setOpen(article, article.classList.contains("collapsed"));
      saveOpen();
      return;
    }
    const form = e.target.closest(".sector-form");
    if (!form) return;
    const slug = form.dataset.slug;
    const mine = (rowFor(slug).myEvents || []).map(({ date, title, impact }) => ({ date, title, impact }));
    const saved = form.querySelector(".saved");
    if (e.target.matches("[data-add]")) {
      const date = form.evDate.value, title = form.evTitle.value.trim();
      if (!date || !title) { saved.textContent = "Give the event a date and a title"; return; }
      saveView(slug, viewOf(form, [...mine, { date, title, impact: form.evImpact.value }]), saved);
    } else if (e.target.matches("[data-remove]")) {
      mine.splice(Number(e.target.dataset.remove), 1);
      saveView(slug, viewOf(form, mine), saved);
    }
  });

  $("cards").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target;
    const mine = (rowFor(form.dataset.slug).myEvents || []).map(({ date, title, impact }) => ({ date, title, impact }));
    saveView(form.dataset.slug, viewOf(form, mine), form.querySelector(".saved"));
  });

  $("inflationForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      render(await api("/api/sectors/settings/inflation", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ inflationPct: Number($("inflation").value) }),
      }));
      $("inflationSaved").textContent = "Saved";
    } catch (err) {
      $("inflationSaved").textContent = err.message;
    }
  });

  $("expandAll").addEventListener("click", () => setAll(true));
  $("collapseAll").addEventListener("click", () => setAll(false));
  $("refresh").addEventListener("click", load);

  const root = document.documentElement;
  const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    root.dataset.theme = isDark() ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  load();
})();
