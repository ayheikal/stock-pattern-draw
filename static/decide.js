(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const STANCE = { for: ["▲", "For"], against: ["▼", "Against"], neutral: ["•", "Neutral"], unknown: ["?", "No data"] };
  const ROLE = { decides: "decides", weighs: "weighs", context: "context only" };
  const CHECK_ICON = { pass: "✓", warn: "!", fail: "✗", unknown: "?" };
  let asked = 0;

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  // Links to the page behind each input; the chart page has the checklist and the rating
  const pageFor = (e, b) => (["checklist", "rating"].includes(e.key) ? `/?${new URLSearchParams({ symbol: b.symbol, interval: "1d", period: "1y" })}`
    : e.key === "value" ? `/value?${new URLSearchParams({ symbol: b.symbol.replace(/^EGX:/, "") })}`
    : e.key === "plan" && b.profileSymbol ? `/profile#card=${encodeURIComponent(b.profileSymbol)}` : e.page);

  function whatToDo(b) {
    const d = b.decision, out = [];
    if (b.ticket) out.push(`<div class="ticket"><span class="ticket-text">${escapeHtml(b.ticket.text)}</span></div>`);
    else if (d.group === "Buy" && b.sizing && b.sizing.ok) {
      out.push(`Buy up to <b>${b.sizing.shares.toLocaleString()}</b> shares with a stop at <b>${fmt(b.sizing.stop)}</b>, risking ${b.sizing.riskPct}% of your capital.`
        + (b.sizing.limitNote ? ` Capped: ${escapeHtml(b.sizing.limitNote)}` : ""));
    } else if (d.group === "Buy" && b.sizing && b.sizing.configured === false) {
      out.push('Set your capital in <a href="/profile">My profile</a> to see how many shares.');
    }
    const l = b.levels;
    if (l && ["Buy", "Hold"].includes(d.group)) {
      const whose = { yours: "your", chart: "the chart's", mixed: "your / the chart's" }[l.source] || "";
      out.push(`<span class="muted">Levels judged (${whose}): target ${fmt(l.target)} (${l.upsidePct >= 0 ? "+" : ""}${l.upsidePct}%), stop ${fmt(l.stop)}, reward/risk ${l.rr} : 1.</span>`);
    }
    return out.length ? `<div class="call-do">${out.join("<br />")}</div>` : "";
  }

  function holdingLine(b) {
    const h = b.holding;
    if (!b.position || !h) return b.inProfile ? '<span class="muted small">In My profile, not held</span>' : '<span class="muted small">Not in My profile</span>';
    const pl = h.plPct == null ? "" : ` · <span class="${h.plPct >= 0 ? "up" : "down"}">${h.plPct >= 0 ? "+" : ""}${h.plPct}%</span>`;
    return `<span class="small">You hold ${Number(h.adjQty || 0).toLocaleString()} @ ${fmt(h.adjAvgCost)}${pl}</span>`;
  }

  function render(b) {
    const d = b.decision, code = b.symbol.replace(/^EGX:/, "").replace(".CA", "");
    const counts = ["for", "against"].map((s) => b.evidence.filter((e) => e.role !== "context" && e.stance === s).length);
    $("call").className = `call-box ${d.tone}`;
    $("call").innerHTML = `
      <div class="call-head">
        <div><div class="call-sym">${escapeHtml(code)} <span class="muted small">${escapeHtml(b.name)}</span></div>
          <div class="small">${fmt(b.last)} ${escapeHtml(b.currency || "")} <span class="${b.changePct >= 0 ? "up" : "down"}">${b.changePct >= 0 ? "+" : ""}${b.changePct}%</span>
            <span class="muted">· last bar ${escapeHtml(b.lastBar || "—")}</span></div>
          <div>${holdingLine(b)}</div></div>
        <div class="call-verdict"><div class="call-label">${escapeHtml(d.label)}</div>
          <div class="muted small">${counts[0]} for · ${counts[1]} against</div></div>
      </div>
      <p class="call-reason">${escapeHtml(d.reason)}</p>
      ${whatToDo(b)}`;
    $("evidence").querySelector("tbody").innerHTML = b.evidence.map((e) => `
      <tr class="${e.role === "context" ? "ctx" : ""}">
        <td><a href="${escapeHtml(pageFor(e, b))}"><b>${escapeHtml(e.source)}</b></a><div class="muted small">${ROLE[e.role]}${e.veto && e.stance === "against" ? " · blocks" : ""}</div></td>
        <td class="nowrap"><b>${escapeHtml(e.reading)}</b></td>
        <td class="nowrap stance ${e.stance}">${STANCE[e.stance][0]} ${STANCE[e.stance][1]}</td>
        <td class="small">${escapeHtml(e.detail)}</td>
      </tr>`).join("");
    $("checks").innerHTML = b.checks.map((k) => `<li class="${k.status}"><span class="ic">${CHECK_ICON[k.status]}</span><span class="lbl">${escapeHtml(k.label)}</span>
      <span class="det">${escapeHtml(k.detail)}</span></li>`).join("");
    $("checksBox").hidden = !b.checks.length;
    $("result").hidden = false;
  }

  async function decide() {
    const sym = $("ticker").value.trim().toUpperCase();
    if (!sym) return;
    const n = ++asked, interval = $("interval").value;
    const u = new URL(location.href);
    u.searchParams.set("symbol", sym);
    u.searchParams.set("interval", interval);
    history.replaceState(null, "", u);
    $("go").disabled = true;
    $("result").hidden = true;
    setStatus(`Running every analysis for ${escapeHtml(sym)}…`);
    try {
      const res = await fetch(`/api/final?${new URLSearchParams({ symbol: sym, interval })}`);
      const b = await res.json();
      if (n !== asked) return; // another ticker was asked meanwhile
      if (!res.ok) throw new Error(typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`);
      setStatus("");
      render(b);
    } catch (err) {
      if (n === asked) setStatus(escapeHtml(err.message), true);
    } finally {
      if (n === asked) $("go").disabled = false;
    }
  }

  // My profile's symbols as one-click chips and suggestions
  async function loadMine() {
    try {
      const r = await fetch("/api/profile?live=false");
      if (!r.ok) return;
      const entries = (await r.json()).entries || [];
      $("mine").innerHTML = entries.map((e) => `<option value="${escapeHtml(e.symbol)}"></option>`).join("");
      // Held: my trades leave shares, or (without trades) the entry says long
      const held = (e) => ((e.trades || []).length
        ? e.trades.reduce((q, t) => q + (t.side === "buy" ? t.qty : -t.qty), 0) > 0 : e.position === "long" && e.qty > 0);
      $("chips").innerHTML = '<span class="muted small">My profile (outlined = held):</span> ' + entries.map((e) =>
        `<button type="button" class="chip${held(e) ? " held" : ""}" title="${held(e) ? "You hold it" : "Watching"}" data-symbol="${escapeHtml(e.symbol)}">${escapeHtml(e.symbol.replace(/^EGX:/, "").replace(".CA", ""))}</button>`).join("");
      $("chips").hidden = !entries.length;
    } catch (_) {}
  }

  // ---------- To buy now: every stock in a list whose final call is a buy ----------
  const COUNT_LABEL = { "strong-buy": "strong buy", buy: "buy", add: "add more", wait: "wait", unknown: "not enough data", "dont-buy": "don't buy",
    hold: "hold", reduce: "reduce", sell: "sell" };
  const halalBadge = (h) => (!h ? "" : h.status === "halal" ? '<span class="halal" title="Halal">Halal</span>' : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');
  let buyAsked = 0;

  function setBuyStatus(msg, isError = false) {
    const el = $("buyStatus");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  function buyRow(r) {
    const code = r.symbol.replace(/^EGX:/, "").replace(".CA", "");
    const shares = r.ticket ? r.ticket.qty : r.sizing && r.sizing.ok ? r.sizing.shares : null;
    const stop = r.sizing && r.sizing.ok ? r.sizing.stop : r.levels ? r.levels.stop : null;
    const l = r.levels;
    const why = r.evidence.filter((e) => e.role === "weighs" && (e.stance === "for" || e.stance === "against"))
      .map((e) => `<span class="${e.stance}">${e.stance === "for" ? "▲" : "▼"} ${escapeHtml(e.source)}: ${escapeHtml(e.reading)}</span>`).join("<br />");
    return `<tr data-symbol="${escapeHtml(r.profileSymbol || r.symbol)}" title="${escapeHtml(r.decision.reason)}">
      <td><div class="sym">${escapeHtml(code)} ${halalBadge(r.halal)}</div><div class="muted small">${escapeHtml(r.name)}</div></td>
      <td><span class="verdict ready">${escapeHtml(r.decision.label)}</span>${r.position ? '<div class="muted small">you hold it</div>' : ""}</td>
      <td class="num">${fmt(r.last)}<div class="small ${r.changePct >= 0 ? "up" : "down"}">${r.changePct >= 0 ? "+" : ""}${r.changePct}%</div></td>
      <td class="num">${shares ? Number(shares).toLocaleString() : "—"}</td>
      <td class="num">${fmt(stop)}</td>
      <td class="num">${l ? `${fmt(l.target)}<div class="muted small">${l.rr} : 1</div>` : "—"}</td>
      <td class="why">${why || '<span class="muted">Checklist only</span>'}</td>
    </tr>`;
  }

  async function loadBuys(refresh = false) {
    const n = ++buyAsked, list = $("buyList").value, interval = $("interval").value;
    try { localStorage.setItem("decide", JSON.stringify({ list })); } catch (_) {}
    $("buyRefresh").disabled = true;
    $("buys").hidden = true;
    $("buyCount").hidden = true;
    $("buyMeta").textContent = "";
    setBuyStatus("Checking every stock in the list with every analysis… about half a minute when the prices aren't loaded yet.");
    try {
      const res = await fetch(`/api/final/buys?${new URLSearchParams({ universe: list, interval, refresh })}`);
      const b = await res.json();
      if (n !== buyAsked) return;
      if (!res.ok) throw new Error(typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`);
      const others = Object.entries(b.counts).filter(([k]) => !["strong-buy", "buy", "add"].includes(k))
        .sort((x, y) => y[1] - x[1]).map(([k, v]) => `${v} ${COUNT_LABEL[k] || k}`);
      const mood = b.mood ? ` · market mood: ${{ strong: "strong", weak: "weak", neutral: "mixed", unknown: "unknown" }[b.mood.key]}` : "";
      $("buyMeta").textContent = `${b.results.length} to buy of ${b.checked} checked${others.length ? ` · ${others.join(" · ")}` : ""}${mood}`
        + ` · as of ${new Date(b.asOf * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
        + (b.skipped.length ? ` · ${b.skipped.length} without data` : "");
      $("buyCount").textContent = b.results.length;
      $("buyCount").hidden = false;
      $("buys").querySelector("tbody").innerHTML = b.results.map(buyRow).join("");
      $("buys").hidden = !b.results.length;
      setBuyStatus(b.results.length ? "" : "Nothing in this list is a buy right now. That's an answer too: most of the time, waiting is the call.");
    } catch (err) {
      if (n === buyAsked) setBuyStatus(escapeHtml(err.message), true);
    } finally {
      if (n === buyAsked) $("buyRefresh").disabled = false;
    }
  }

  async function loadLists() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("decide") || "{}").list; } catch (_) {}
    try {
      const r = await fetch("/api/universes");
      if (r.ok) {
        const u = await r.json();
        $("buyList").innerHTML = '<option value="profile">My profile</option>' + Object.entries(u)
          .map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(v.label)} (${v.count})</option>`).join("");
      }
    } catch (_) {}
    const has = (v) => [...$("buyList").options].some((o) => o.value === v);
    $("buyList").value = has(saved) ? saved : has("egx_top") ? "egx_top" : $("buyList").options[0].value;
    loadBuys();
  }

  $("buyList").addEventListener("change", () => loadBuys());
  $("buyRefresh").addEventListener("click", () => loadBuys(true));
  $("buys").querySelector("tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    if (!tr) return;
    $("ticker").value = tr.dataset.symbol;
    decide().then(() => $("result").scrollIntoView({ behavior: "smooth", block: "start" }));
  });

  $("ask").addEventListener("submit", (e) => { e.preventDefault(); decide(); });
  $("interval").addEventListener("change", () => { if ($("ticker").value.trim()) decide(); loadBuys(); });
  $("chips").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-symbol]");
    if (b) { $("ticker").value = b.dataset.symbol; decide(); }
  });

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  loadMine();
  loadLists();
  const q = new URLSearchParams(location.search); // deep link: /decide?symbol=COMI.CA
  if (q.get("interval")) $("interval").value = q.get("interval") === "1wk" ? "1wk" : "1d";
  if (q.get("symbol")) { $("ticker").value = q.get("symbol"); decide(); }
})();
