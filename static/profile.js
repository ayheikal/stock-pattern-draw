(() => {
  const $ = (id) => document.getElementById(id);
  const state = { entries: [], editing: null, open: new Set() };
  try { state.open = new Set(JSON.parse(localStorage.getItem("profileOpen") || "[]")); } catch (_) {}
  const saveOpen = () => { try { localStorage.setItem("profileOpen", JSON.stringify([...state.open])); } catch (_) {} };
  const FIELDS = ["symbol", "position", "qty", "avgCost", "expectation", "target", "stop", "horizon", "note"];
  const NUMERIC = ["qty", "avgCost", "target", "stop"];
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const pct = (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const cls = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : d || `Request failed (${res.status})`);
    }
    return body;
  }

  async function load() {
    const interval = $("interval").value;
    try { localStorage.setItem("profileInterval", interval); } catch (_) {}
    $("refresh").disabled = true;
    setStatus("Checking live prices and chart outlooks…");
    try {
      const body = await api(`/api/profile?interval=${interval}`);
      if (document.activeElement !== $("profileName")) $("profileName").value = body.name || "";
      fillSettings(body.settings);
      state.entries = body.entries;
      state.summary = body.summary;
      render();
      setStatus(body.entries.length ? "" : "Your profile is empty. Add the indices and stocks you follow above.");
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      $("refresh").disabled = false;
    }
  }

  function render() {
    const es = state.entries;
    $("count").textContent = es.length;
    renderSummary(state.summary);
    $("cards").innerHTML = es.map(card).join("");
  }

  // Proportion bar + legend for a {key: count} breakdown.
  function breakdown(counts, labels) {
    const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
    const keys = Object.keys(labels);
    return `
      <div class="split">${keys.map((k) => (counts[k] ? `<span class="seg ${k}" style="flex:${counts[k]}"></span>` : "")).join("")}</div>
      <div class="split-legend">${keys.map((k) => `<span><i class="sw ${k}"></i>${labels[k]} <b>${counts[k]}</b></span>`).join("")}</div>`;
  }

  function riskTile(r) {
    if (!r) return `<div class="tile"><h3>Risk</h3><div class="muted small">Enter your capital under <a href="#settingsForm">My money &amp; rules</a> to see how much you have at risk.</div></div>`;
    const cur = escapeHtml(r.currency);
    const over = r.atRiskPct > r.riskPct * 3;
    return `<div class="tile">
        <h3>Risk</h3>
        <div class="big ${over ? "down" : ""}">${r.atRiskPct}%</div>
        <div class="muted small">of your capital is lost if every stop is hit (${fmt(r.atRisk)} ${cur})</div>
        <div class="kv"><span>Invested</span><span>${fmt(r.invested)} ${cur} (${r.investedPct}%)</span></div>
        <div class="kv"><span>Cash</span><span class="${r.cash < 0 ? "down" : ""}">${fmt(r.cash)} ${cur}</span></div>
        ${r.noStop.length ? `<div class="kv"><span>No stop</span><span class="down">${r.noStop.map(escapeHtml).join(", ")}</span></div>` : ""}
      </div>`;
  }

  function renderSummary(s) {
    $("summaryPanel").hidden = !s || !s.count;
    if (!s || !s.count) return;
    const money = (v) => `${v > 0 ? "+" : ""}${fmt(v)}`;
    const books = s.byCurrency.length
      ? s.byCurrency.map((b) => `
          <div class="book">
            <div class="big">${fmt(b.value)} <span class="unit">${escapeHtml(b.currency || "pts")}</span></div>
            <div class="${cls(b.pl)}">${money(b.pl)} (${pct(b.plPct)})</div>
            <div class="muted small">cost ${fmt(b.cost)} · ${b.positions} position${b.positions === 1 ? "" : "s"}</div>
          </div>`).join("")
      : '<div class="muted small">Add a quantity and average cost to your positions to see value and P/L.</div>';
    const perf = (label, p) => (p ? `<div class="kv"><span>${label}</span><span><b>${escapeHtml(p.symbol)}</b> <span class="${cls(p.plPct)}">${pct(p.plPct)}</span></span></div>` : "");

    const todo = s.todo || {};
    const tagOf = Object.fromEntries(state.entries.filter((e) => e.action).map((e) => [e.symbol, e.action.action]));
    const todoRow = (k, label) => (todo[k] && todo[k].length
      ? `<div class="todo-row"><span class="act ${k}">${label}</span><span>${todo[k].map((x) => {
          const tag = tagOf[x] && tagOf[x] !== label ? ` <span class="muted small">(${escapeHtml(tagOf[x])})</span>` : "";
          return `<a href="#" data-goto="${escapeHtml(x)}">${escapeHtml(x)}</a>${tag}`;
        }).join(", ")}</span></div>` : "");
    $("tiles").innerHTML = `
      <div class="tile todo">
        <h3>What to do</h3>
        ${todoRow("sell", "Sell")}${todoRow("buy", "Buy")}${todoRow("hold", "Hold")}${todoRow("wait", "Wait")}
        <div class="muted small">From your own view, stop and target, and your money rules. Not from the chart signals.</div>
      </div>
      <div class="tile">
        <h3>Portfolio</h3>
        ${books}
      </div>
      <div class="tile">
        <h3>Following</h3>
        <div class="big">${s.count}</div>
        <div class="muted small">${s.holding} held · ${s.watching} watching</div>
        ${perf("Best", s.best)}${perf("Worst", s.worst)}
      </div>
      ${riskTile(s.risk)}
      <div class="tile">
        <h3>My views</h3>
        ${breakdown(s.expectation, { bullish: "Bullish", neutral: "Neutral", bearish: "Bearish" })}
      </div>
      <div class="tile">
        <h3>Chart vs me</h3>
        ${breakdown(s.agreement, { agrees: "Agrees", mixed: "Mixed", disagrees: "Disagrees" })}
      </div>`;

    const icons = { stop: "⛔", target: "🎯", "near-stop": "⚠️", disagree: "✗", error: "?", big: "⚖️", risk: "💸", "no-stop": "🛑" };
    $("attention").innerHTML = s.attention.length
      ? `<h3>Needs attention <span class="badge">${s.attention.length}</span></h3>
         <ul>${s.attention.map((a) => `<li class="${a.kind}"><span class="ic">${icons[a.kind] || "•"}</span><a href="#" data-goto="${escapeHtml(a.symbol)}">${escapeHtml(a.symbol)}</a> ${escapeHtml(a.text)}</li>`).join("")}</ul>`
      : '<p class="all-clear">✓ Nothing needs attention: no stops or targets hit, and the chart doesn\'t contradict any position you hold.</p>';
  }

  $("tiles").addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-goto]");
    if (!a) return;
    ev.preventDefault();
    goTo(a.dataset.goto);
  });

  function goTo(symbol) {
    const el = [...document.querySelectorAll(".card")].find((c) => c.dataset.symbol === symbol);
    if (!el) return;
    setOpen(el, true);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1200);
  }

  $("attention").addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-goto]");
    if (!a) return;
    ev.preventDefault();
    goTo(a.dataset.goto);
  });

  // How the chart's current bias has played out on this symbol before (from the Track record page).
  function trackLine(t) {
    if (!t) return "";
    const what = t.asExpected == null
      ? `price moved ${pct(t.avgRet)} on average, vs ${pct(t.baseline)} for any period`
      : `price went the predicted way <b>${t.asExpected.toFixed(0)}%</b> of the time, avg ${pct(t.avgRet)} vs ${pct(t.baseline)} for any period`;
    return `<div class="track-line" title="From the Track record page">📊 After past <b>${escapeHtml(t.bias)}</b> readings here (${t.n}${t.small ? ", few" : ""}): ${what}. <a href="/track">Details</a></div>`;
  }

  const SETTINGS = ["capital", "currency", "riskPct", "maxPositionPct"];
  function fillSettings(st) {
    if (!st || $("settingsForm").contains(document.activeElement)) return;
    for (const k of SETTINGS) $(`s-${k}`).value = st[k] ?? "";
  }

  // What to buy (watching) or what the position means for the account (holding).
  function moneyBlock(e) {
    const cur = escapeHtml(e.sizing?.currency || e.currency || "");
    const x = e.exposure;
    if (x) {
      const loss = x.risk == null
        ? '<span class="down">No stop-loss set, so there is no limit on what this can cost you.</span>'
        : e.status === "stop hit"
        ? `<span class="down">The price is already past your stop ${fmt(e.stop)}, so the stop no longer protects you. Decide whether to exit, or set a new stop.</span>`
        : `If your stop ${fmt(e.stop)} is hit you lose about <b class="down">${fmt(x.risk)} ${cur}</b> (${x.riskPct}% of your capital).`;
      return `<div class="card-block money"><h3>My money</h3>
        This position is worth <b>${fmt(x.value)} ${cur}</b>, <b>${x.weightPct}%</b> of your capital. ${loss}</div>`;
    }
    const z = e.sizing;
    if (!z) return "";
    if (!z.ok) {
      const link = z.configured === false ? ' <a href="#settingsForm">Set it now</a>' : "";
      return `<div class="card-block money"><h3>Position size</h3><span class="muted">${escapeHtml(z.reason)}</span>${link}</div>`;
    }
    const whose = z.stopSource === "yours" ? "your stop" : "the chart's suggested stop";
    return `<div class="card-block money"><h3>Position size</h3>
      Buy up to <b>${z.shares.toLocaleString()} shares</b> (about ${fmt(z.cost)} ${cur}, ${z.costPct}% of your capital),
      with ${whose} at <b class="stp">${fmt(z.stop)}</b> (${pct(z.stopPct)}).
      If it's hit you lose about <b class="down">${fmt(z.risk)} ${cur}</b> (${z.riskPct}% of your capital).
      ${z.limitedBy === "size" ? `<div class="muted small">Capped by your limit per stock; the stop is close, so the risk is below your maximum.</div>` : ""}</div>`;
  }

  function card(e) {
    const posText = e.position === "none"
      ? '<span class="muted">No position (watching)</span>'
      : `<b>${cap(e.position)}</b>${e.qty != null ? ` ${fmt(e.qty)}` : ""}${e.avgCost != null ? ` @ ${fmt(e.avgCost)}` : ""}`;
    const pl = e.plPct != null
      ? `<div class="kv"><span>P/L</span><span class="${cls(e.plPct)}">${pct(e.plPct)}${e.plValue != null ? ` (${e.plValue > 0 ? "+" : ""}${fmt(e.plValue)})` : ""}</span></div>` : "";
    const lvl = (label, v, d, c) => (v != null ? `<div class="kv"><span>${label}</span><span><b class="${c}">${fmt(v)}</b> <span class="muted small">${pct(d)}</span></span></div>` : "");
    const statusTag = e.status && e.status !== "open" ? `<span class="tag ${e.status === "target hit" ? "bullish" : "bearish"}">${e.status}</span>` : "";
    const agreeTag = e.agreement ? `<span class="agree ${e.agreement}">${{ agrees: "✓ Chart agrees", disagrees: "✗ Chart disagrees", mixed: "~ Mixed" }[e.agreement]}</span>` : "";
    const chartUrl = `/?${new URLSearchParams({ symbol: e.resolved || e.symbol, interval: $("interval").value, period: $("interval").value === "1wk" ? "5y" : "1y" })}`;

    const live = e.error
      ? `<div class="status error inline">Could not load data for this symbol (${escapeHtml(e.error)}).</div>`
      : `<div class="card-block chart-says">
          <h3>Chart says</h3>
          <div class="kvs">
            <div class="kv"><span>Outlook</span><span><span class="bias ${e.appView}">${escapeHtml(e.appBias)}</span> <span class="muted small">${e.appScore.toFixed(2)}</span></span></div>
            ${lvl(e.expectation === "bearish" ? "Down target" : "Up target", e.appTarget, e.appTarget && ((e.appTarget - e.last) / e.last) * 100, "")}
            <div class="kv"><span>Newcomer</span><span class="action ${/buy/i.test(e.appAction) ? "good" : "caution"}">${escapeHtml(e.appAction)}</span></div>
          </div>
          ${e.appSignal ? `<div class="muted small signal">${escapeHtml(e.appSignal)}</div>` : ""}
          ${trackLine(e.track)}
        </div>`;

    const open = state.open.has(e.symbol);
    const mini = [
      e.position === "none" ? "Watching" : `${cap(e.position)}${e.qty != null ? ` ${fmt(e.qty)}` : ""}${e.avgCost != null ? ` @ ${fmt(e.avgCost)}` : ""}`,
      e.plPct != null ? `<span class="${cls(e.plPct)}">${pct(e.plPct)}</span>` : "",
      e.stop != null ? `stop <b class="stp">${fmt(e.stop)}</b>${e.toStopPct != null ? ` <span class="muted">${pct(e.toStopPct)}</span>` : ""}` : "",
      e.target != null ? `target <b class="tgt">${fmt(e.target)}</b>${e.toTargetPct != null ? ` <span class="muted">${pct(e.toTargetPct)}</span>` : ""}` : "",
    ].filter(Boolean).join(" · ");

    return `
      <article class="card ${e.expectation} ${open ? "" : "collapsed"}" data-symbol="${escapeHtml(e.symbol)}">
        <header class="card-head" data-act="toggle" title="${open ? "Hide details" : "Show details"}">
          <button type="button" class="chev" data-act="toggle" aria-expanded="${open}" aria-label="Toggle details">▸</button>
          <div class="head-main">
            <a class="sym" href="${chartUrl}">${escapeHtml(e.symbol)}</a>${e.action ? ` <span class="act ${e.action.tone}" title="${escapeHtml(e.action.group)}">${escapeHtml(e.action.action)}</span>` : ""}
            <div class="muted small">${escapeHtml(e.name || "")}</div>
          </div>
          <div class="price">
            ${e.last != null ? `<b>${fmt(e.last)}</b> <span class="${cls(e.changePct)} small">${pct(e.changePct)}</span>` : ""}
            <div class="muted small">${escapeHtml(e.currency || "")}</div>
          </div>
        </header>
        ${e.action ? `<div class="action-bar ${e.action.tone}"><b>${escapeHtml(e.action.group)}:</b> ${escapeHtml(e.action.reason)}</div>` : ""}
        <div class="card-mini">${mini}</div>
        <div class="card-details">
        <div class="tags">${agreeTag}${statusTag}</div>
        <div class="card-body">
          <div class="card-block">
            <h3>My position</h3>
            <div>${posText}</div>
            ${pl}
          </div>
          <div class="card-block">
            <h3>My expectation</h3>
            <div class="kv"><span>Direction</span><span class="bias ${e.expectation}">${cap(e.expectation)}</span></div>
            ${lvl("Target", e.target, e.toTargetPct, "tgt")}
            ${lvl("Stop", e.stop, e.toStopPct, "stp")}
            ${e.horizon ? `<div class="kv"><span>Horizon</span><span>${escapeHtml(e.horizon)}</span></div>` : ""}
          </div>
          ${live}
          ${e.error ? "" : moneyBlock(e)}
        </div>
        ${e.note ? `<p class="note">${escapeHtml(e.note)}</p>` : ""}
        <footer class="card-actions">
          <a href="${chartUrl}">Open chart</a>
          <button type="button" data-act="edit">Edit</button>
          <button type="button" data-act="delete" class="danger">Remove</button>
        </footer>
        </div>
      </article>`;
  }

  function fillForm(e) {
    state.editing = e ? e.symbol : null;
    for (const k of FIELDS) {
      const el = $(`f-${k}`);
      el.value = e ? e[k] ?? "" : { position: "none", expectation: "neutral" }[k] ?? "";
    }
    $("f-symbol").readOnly = !!e;
    $("formTitle").textContent = e ? `Edit ${e.symbol}` : "Add an index or stock";
    $("cancelEdit").hidden = !e;
    if (e) $("entryForm").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("entryForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const entry = {};
    for (const k of FIELDS) {
      const v = $(`f-${k}`).value.trim();
      entry[k] = NUMERIC.includes(k) ? (v === "" ? null : Number(v)) : v;
    }
    const btn = ev.submitter || $("entryForm").querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api("/api/profile/entries", { method: "PUT", body: JSON.stringify(entry) });
      fillForm(null);
      await load();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  });

  $("cancelEdit").addEventListener("click", () => fillForm(null));

  $("settingsForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    for (const k of SETTINGS) {
      const v = $(`s-${k}`).value.trim();
      body[k] = k === "currency" ? v || "EGP" : v === "" ? null : Number(v);
    }
    if (body.riskPct == null) delete body.riskPct;
    if (body.maxPositionPct == null) delete body.maxPositionPct;
    try {
      await api("/api/profile/settings", { method: "PUT", body: JSON.stringify(body) });
      $("settingsSaved").textContent = "Saved ✓";
      setTimeout(() => ($("settingsSaved").textContent = ""), 2000);
      await load();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
  });

  function setOpen(article, open) {
    const sym = article.dataset.symbol;
    article.classList.toggle("collapsed", !open);
    article.querySelector(".chev").setAttribute("aria-expanded", String(open));
    article.querySelector(".card-head").title = open ? "Hide details" : "Show details";
    if (open) state.open.add(sym); else state.open.delete(sym);
    saveOpen();
  }

  $("cards").addEventListener("click", async (ev) => {
    const toggle = ev.target.closest("[data-act=toggle]");
    if (toggle && !ev.target.closest("a")) {
      const article = toggle.closest(".card");
      setOpen(article, article.classList.contains("collapsed"));
      return;
    }
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const sym = btn.closest(".card").dataset.symbol;
    const e = state.entries.find((x) => x.symbol === sym);
    if (btn.dataset.act === "edit") return fillForm(e);
    if (!confirm(`Remove ${sym} from your profile?`)) return;
    try {
      await api(`/api/profile/entries/${encodeURIComponent(sym)}`, { method: "DELETE" });
      if (state.editing === sym) fillForm(null);
      await load();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
  });

  $("profileName").addEventListener("change", () => {
    api("/api/profile/name", { method: "PUT", body: JSON.stringify({ name: $("profileName").value }) })
      .catch((err) => setStatus(escapeHtml(err.message), true));
  });
  $("interval").addEventListener("change", load);
  const setAll = (open) => {
    document.querySelectorAll(".card").forEach((c) => setOpen(c, open));
  };
  $("expandAll").addEventListener("click", () => setAll(true));
  $("collapseAll").addEventListener("click", () => setAll(false));
  $("refresh").addEventListener("click", load);

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  try { const i = localStorage.getItem("profileInterval"); if (i) $("interval").value = i; } catch (_) {}
  load();
})();
