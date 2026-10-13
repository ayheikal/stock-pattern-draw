(() => {
  const $ = (id) => document.getElementById(id);
  const state = { entries: [], editing: null, open: new Set() };
  try { state.open = new Set(JSON.parse(localStorage.getItem("profileOpen") || "[]")); } catch (_) {}
  const saveOpen = () => { try { localStorage.setItem("profileOpen", JSON.stringify([...state.open])); } catch (_) {} };
  const FIELDS = ["symbol", "position", "qty", "avgCost", "since", "expectation", "target", "stop", "horizon", "note"];
  const NUMERIC = ["qty", "avgCost", "target", "stop"];
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const pct = (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const cls = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const halalBadge = (h) => (!h ? "" : h.status === "halal"
    ? `<span class="halal${h.source === "mine" ? " mine" : ""}" title="${h.source === "mine" ? "Halal: your own tag" : "In the EGX 33 Shariah list"}">Halal</span>`
    : '<span class="halal not mine" title="Not halal: your own tag">Not halal</span>');

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
      const want = decodeURIComponent((location.hash.match(/^#card=(.+)$/) || [])[1] || "");
      if (want) {
        history.replaceState(null, "", location.pathname); // only once
        goTo(want);
      }
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

  function yearTile(years) {
    if (!years || !years.length) return "";
    const y = String(new Date().getFullYear());
    const now = years.filter((r) => r.year === y);
    const line = (r) => {
      const net = r.realized + r.dividends;
      return `<div class="kv"><span>Realized</span><span class="${cls(r.realized)}">${fmt(r.realized)} ${escapeHtml(r.currency)}</span></div>
        <div class="kv"><span>Dividends</span><span class="up">${fmt(r.dividends)}</span></div>
        <div class="kv"><span>Fees paid</span><span>${fmt(r.fees)}</span></div>
        <div class="kv"><span><b>Net</b></span><b class="${cls(net)}">${fmt(net)}</b></div>`;
    };
    const past = years.filter((r) => r.year !== y).map((r) => `${r.year}: ${fmt(r.realized + r.dividends)} ${escapeHtml(r.currency)}`).join(" · ");
    return `<div class="tile"><h3>${y} so far</h3>${now.length ? now.map(line).join("") : '<div class="muted small">No trades or dividends this year.</div>'}
      ${past ? `<div class="muted small" style="margin-top:6px">Earlier (realized + dividends): ${past}</div>` : ""}</div>`;
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
            ${b.usdPct != null ? `<div class="small">In USD <b class="${cls(b.usdPct)}">${pct(b.usdPct)}</b></div>` : ""}
            ${b.income ? `<div class="small">+ dividends <b class="up">${fmt(b.income)}</b> → total <b class="${cls(b.totalPct)}">${pct(b.totalPct)}</b></div>` : ""}
            <div class="muted small">cost ${fmt(b.cost)} incl. fees · ${b.positions} position${b.positions === 1 ? "" : "s"}</div>
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
      ${yearTile(s.years)}
      <div class="tile">
        <h3>My views</h3>
        ${breakdown(s.expectation, { bullish: "Bullish", neutral: "Neutral", bearish: "Bearish" })}
      </div>
      <div class="tile">
        <h3>Chart vs me</h3>
        ${breakdown(s.agreement, { agrees: "Agrees", mixed: "Mixed", disagrees: "Disagrees" })}
      </div>`;

    const icons = { stop: "⛔", target: "🎯", "near-stop": "⚠️", disagree: "✗", error: "?", big: "⚖️", risk: "💸", "no-stop": "🛑", thin: "💧", date: "📅" };
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

  const SETTINGS = ["capital", "currency", "riskPct", "maxPositionPct", "maxAdtvPct", "feePct", "dividendTaxPct",
    "gainsTaxPct", "minAdtv", "tickSize", "telegramChatId", "alertEveryMin", "backupDir"];
  function fillSettings(st) {
    if (!st || $("settingsForm").contains(document.activeElement)) return;
    for (const k of SETTINGS) $(`s-${k}`).value = st[k] ?? "";
    $("s-alertsOn").checked = !!st.alertsOn;
    $("s-holidays").value = (st.holidays || []).join(", ");
    $("s-telegramToken").value = "";
    $("s-telegramToken").placeholder = st.hasTelegramToken ? "saved ✓ (leave empty to keep)" : "from @BotFather";
    $("costsSummary").textContent = `${st.feePct}% per trade · ${st.dividendTaxPct}% dividend tax`;
    $("alertsSummary").textContent = st.alertsOn ? (st.hasTelegramToken && st.telegramChatId ? "on" : "on, not set up yet") : "off";
  }

  // Phone app: a QR code that opens the app on the phone, already logged in (phone mode only).
  function loadScript(src) {
    return new Promise((ok, fail) => {
      const el = document.createElement("script");
      el.src = src;
      el.onload = ok;
      el.onerror = fail;
      document.head.appendChild(el);
    });
  }

  async function refreshPhone() {
    let p;
    try { p = await api("/api/phone"); } catch (_) { $("phoneGroup").hidden = true; return; } // opened from the phone itself
    $("phoneSummary").textContent = p.lan ? "on" : "off";
    if (!p.lan) {
      $("phoneBody").innerHTML = `<p class="muted small">Phone access is off: the app only answers on this computer.
        It's switched on when the app runs in the background with <code>--lan</code> (the Windows auto-start does this).
        Your phone must be on the same Wi-Fi as this computer.</p>`;
      return;
    }
    $("phoneBody").innerHTML = `
      <div class="phone-row">
        <div id="qr" class="qr" aria-label="QR code"></div>
        <ol class="muted small setup-steps">
          <li>Connect your phone to the <b>same Wi-Fi</b> as this computer.</li>
          <li>Scan this code with the phone camera. It opens the app and remembers the phone (no password to type).</li>
          <li><b>Android</b> (Chrome): menu ⋮ → <b>Add to Home screen</b>. <b>iPhone</b> (Safari): Share → <b>Add to Home Screen</b>.</li>
          <li>This computer must be on and the app running; the phone reaches it at <code>${escapeHtml(p.base)}</code>.</li>
        </ol>
      </div>
      <p class="muted small">The code contains your access key, so don't share it. To change the key, delete <code>data/access_key.txt</code> and restart the app.</p>`;
    try {
      if (!window.qrcode) await loadScript("https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js");
      const qr = window.qrcode(0, "M");
      qr.addData(p.url);
      qr.make();
      $("qr").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
    } catch (_) {
      $("qr").innerHTML = `<div class="muted small">Couldn't draw the QR code. Open this address on the phone instead:<br><code>${escapeHtml(p.url)}</code></div>`;
    }
  }

  async function refreshBackups() {
    try {
      const b = await api("/api/backups");
      $("backupFolder").textContent = b.folder;
      $("backupList").innerHTML = b.files.map((f) => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name.replace("profile-", "").replace(".json", ""))} · saved ${new Date(f.saved).toLocaleString()}</option>`).join("")
        || "<option value=''>No copies yet</option>";
      $("backupSummary").textContent = b.files.length ? `${b.files.length} copies · latest ${new Date(b.files[0].saved).toLocaleDateString()}` : "";
      $("backupStatus").innerHTML = b.error ? `<span class="down">${escapeHtml(b.error)}</span>` : "";
    } catch (_) {}
  }

  // Halal list: the EGX 33 Shariah members I pasted in, and my own tags.
  async function refreshShariah(b) {
    try {
      b = b || await api("/api/shariah");
      state.shariahList = b;
      const n = b.symbols.length, t = b.tags.length;
      $("shariahSummary").innerHTML = [n ? `${n} stocks${b.listDate ? ` · list from ${escapeHtml(b.listDate)}` : ""}` : "no list yet",
        t ? `${t} own tag${t === 1 ? "" : "s"}` : "", b.stale ? '<span class="down">may be out of date</span>' : ""].filter(Boolean).join(" · ");
      if (b.listDate) $("shariahDate").value = b.listDate;
      const chip = (x, act, label) => `<span class="chip" title="${escapeHtml(x.name || x.symbol)}">${label}<button type="button" data-act="${act}" data-symbol="${escapeHtml(x.symbol)}" aria-label="Remove ${escapeHtml(x.symbol)}">×</button></span>`;
      $("shariahList").innerHTML = b.symbols.map((x) => chip(x, "unlist", escapeHtml(x.symbol.replace(/\.CA$/, "")))).join("");
      $("shariahTags").innerHTML = b.tags.map((x) => chip(x, "untag", `${escapeHtml(x.symbol)} ${halalBadge({ status: x.status, source: "mine" })}`)).join("")
        || '<span class="muted small">None.</span>';
      return b;
    } catch (_) {}
  }

  async function saveShariah(text) {
    const btn = $("shariahSave");
    btn.disabled = true;
    $("shariahStatus").textContent = "Saving…";
    try {
      const b = await api("/api/shariah/list", { method: "PUT", body: JSON.stringify({ text, listDate: $("shariahDate").value || null }) });
      await refreshShariah(b);
      $("shariahText").value = "";
      $("shariahStatus").innerHTML = [b.symbols.length ? `Saved ${b.symbols.length} stocks ✓` : "List cleared ✓",
        b.unknown?.length ? `<span class="down">Not recognised: ${escapeHtml(b.unknown.join(", "))}</span>` : "",
        b.checked === false ? "Couldn't reach the EGX stock list, so the codes weren't checked." : ""].filter(Boolean).join(" · ");
      load();
    } catch (err) {
      $("shariahStatus").innerHTML = `<span class="down">${escapeHtml(err.message)}</span>`;
    } finally {
      btn.disabled = false;
    }
  }

  async function refreshAlertStatus() {
    try {
      const a = await api("/api/alerts/status");
      const t = (iso) => (iso ? new Date(iso).toLocaleString() : "never");
      $("alertStatus").innerHTML = a.lastError
        ? `<span class="down">Last problem: ${escapeHtml(a.lastError)}</span>`
        : `Last check: ${t(a.lastCheck)} · last message: ${t(a.lastSent)}${a.inSession ? " · market open" : " · market closed"}`;
    } catch (_) {}
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
      ${z.limitedBy === "size" ? `<div class="muted small">Capped by your limit per stock; the stop is close, so the risk is below your maximum.</div>` : ""}
      ${z.limitedBy === "liquidity" ? `<div class="muted small">💧 Capped by liquidity: the stock trades about ${fmt(e.adtv)} a day, and you allow at most your set share of that.</div>` : ""}
      ${z.feePct ? `<div class="muted small">Includes ${z.feePct}% costs on the buy and on the sell.</div>` : ""}</div>`;
  }

  // Bonus shares / splits since the buy date, and a nudge when the date is missing.
  function corpLines(e) {
    const c = e.corporate;
    if (!c) return "";
    if (c.needsDate) return '<div class="corp muted small">📅 Add your buy date (Edit) so bonus shares and dividends are counted.</div>';
    const splits = (c.events || []).filter((x) => x.kind !== "dividend");
    if (!splits.length) return "";
    const what = splits.map((x) => (x.kind === "bonus" ? `+${Math.round((x.ratio - 1) * 100)}% bonus shares` : `${x.ratio}:1 split`) + ` on ${x.date}`).join(", ");
    return `<div class="corp small">Now <b>${fmt(e.adjQty)}</b> shares @ <b>${fmt(e.adjAvgCost)}</b>, adjusted for ${escapeHtml(what)}.</div>`;
  }

  // The same holding in dollars: separates a real gain from the pound losing value.
  function usdLine(u) {
    if (!u) return "";
    const pound = u.egpChangePct < 0 ? `the pound fell ${Math.abs(u.egpChangePct).toFixed(1)}%` : `the pound rose ${u.egpChangePct.toFixed(1)}%`;
    return `<div class="kv" title="Bought at ${u.rateThen} EGP per USD, now ${u.rateNow}"><span>In USD</span><span class="${cls(u.plUsdPct)}">${pct(u.plUsdPct)}</span></div>
      <div class="muted small">${pound} against the dollar since you bought</div>`;
  }

  const today = () => new Date().toISOString().slice(0, 10);
  function tradesBlock(e) {
    const j = e.journal;
    const rows = (e.trades || []).map((t, i) => `
      <tr><td>${escapeHtml(t.date)}</td><td class="${t.side === "buy" ? "up" : "down"}">${t.side.toUpperCase()}</td>
        <td class="num">${fmt(t.qty)} @ ${fmt(t.price)}</td><td class="num muted">${t.fees != null ? `fees ${fmt(t.fees)}` : "est. fees"}</td>
        <td><button type="button" class="x" data-act="del-trade" data-i="${i}" title="Delete this trade">×</button></td></tr>`).join("");
    const sums = j ? `<div class="muted small">Fees paid ${fmt(j.fees)}${j.dividends ? ` · dividends ${fmt(j.dividends)}` : ""}${j.realized ? ` · realized ${j.realized > 0 ? "+" : ""}${fmt(j.realized)}` : ""}</div>` : "";
    const warn = (j?.warnings || []).map((w) => `<div class="thin-note">⚠ ${escapeHtml(w)}</div>`).join("");
    return `<div class="card-block trades"><h3>Trades ${e.trades?.length ? "" : '<span class="muted small">(optional: record buys and sells for exact P/L)</span>'}</h3>
      ${rows ? `<table class="trade-list"><tbody>${rows}</tbody></table>` : ""}
      ${sums}${warn}
      <form class="trade-form" autocomplete="off">
        <input name="date" type="date" value="${today()}" required />
        <select name="side"><option value="buy">Buy</option><option value="sell">Sell</option></select>
        <input name="qty" type="number" min="0" step="any" placeholder="Qty" required />
        <input name="price" type="number" min="0" step="any" placeholder="Price" required />
        <input name="fees" type="number" min="0" step="any" placeholder="Fees (optional)" title="From the contract note; leave empty to estimate" />
        <button type="submit">Add trade</button>
      </form>
      ${e.trades?.length ? '<div class="muted small">Quantity, average cost and buy date now come from these trades.</div>' : ""}</div>`;
  }

  function card(e) {
    const posText = e.position === "none"
      ? '<span class="muted">No position (watching)</span>'
      : e.trades?.length
      ? `<b>${cap(e.position)}</b> ${fmt(e.adjQty)} @ ${fmt(e.adjAvgCost)} <span class="muted small">from ${e.trades.length} trade${e.trades.length === 1 ? "" : "s"}</span>`
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
      e.position === "none" ? "Watching" : `${cap(e.position)}${e.qty != null ? ` ${fmt(e.adjQty ?? e.qty)}` : ""}${e.avgCost != null ? ` @ ${fmt(e.adjAvgCost ?? e.avgCost)}` : ""}`,
      e.plPct != null ? `<span class="${cls(e.plPct)}">${pct(e.plPct)}</span>` : "",
      e.stop != null ? `stop <b class="stp">${fmt(e.stop)}</b>${e.toStopPct != null ? ` <span class="muted">${pct(e.toStopPct)}</span>` : ""}` : "",
      e.target != null ? `target <b class="tgt">${fmt(e.target)}</b>${e.toTargetPct != null ? ` <span class="muted">${pct(e.toTargetPct)}</span>` : ""}` : "",
    ].filter(Boolean).join(" · ");

    return `
      <article class="card ${e.expectation} ${open ? "" : "collapsed"}" data-symbol="${escapeHtml(e.symbol)}">
        <header class="card-head" data-act="toggle" title="${open ? "Hide details" : "Show details"}">
          <button type="button" class="chev" data-act="toggle" aria-expanded="${open}" aria-label="Toggle details">▸</button>
          <div class="head-main">
            <a class="sym" href="${chartUrl}">${escapeHtml(e.symbol)}</a>${e.action ? ` <span class="act ${e.action.tone}" title="${escapeHtml(e.action.group)}">${escapeHtml(e.action.action)}</span>` : ""} ${halalBadge(e.halal)}
            <div class="muted small">${escapeHtml(e.name || "")}</div>
          </div>
          <div class="price">
            ${e.last != null ? `<b>${fmt(e.last)}</b> <span class="${cls(e.changePct)} small">${pct(e.changePct)}</span>` : ""}
            <div class="muted small">${escapeHtml(e.currency || "")}</div>
          </div>
        </header>
        ${e.action ? `<div class="action-bar ${e.action.tone}"><b>${escapeHtml(e.action.group)}:</b> ${escapeHtml(e.action.reason)}</div>` : ""}
        ${e.ticket ? `<div class="ticket"><span class="ticket-text">📋 ${escapeHtml(e.ticket.text)}</span><button type="button" data-act="copy" data-text="${escapeHtml(e.ticket.text)}">Copy</button></div>` : ""}
        <div class="card-mini">${mini}</div>
        <div class="card-details">
        <div class="tags">${agreeTag}${statusTag}</div>
        <div class="card-body">
          <div class="card-block">
            <h3>My position</h3>
            <div>${posText}</div>
            ${corpLines(e)}
            ${pl}
            ${e.breakEven != null ? `<div class="kv"><span>Break-even</span><span title="Price that covers your buying and selling costs">${fmt(e.breakEven)}</span></div>` : ""}
            ${e.income ? `<div class="kv"><span>Dividends</span><span class="up">+${fmt(e.income)}</span></div>
              <div class="kv"><span>Total return</span><span class="${cls(e.totalReturn)}">${e.totalReturn > 0 ? "+" : ""}${fmt(e.totalReturn)}</span></div>` : ""}
            ${e.journal && (e.journal.realized || e.journal.fees) ? `<div class="kv"><span>Realized</span><span class="${cls(e.journal.realized)}">${e.journal.realized > 0 ? "+" : ""}${fmt(e.journal.realized)}</span></div>` : ""}
            ${usdLine(e.usd)}
            ${e.thin ? `<div class="thin-note">💧 Thinly traded: about ${fmt(e.adtv)} a day.</div>` : ""}
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
          ${e.error ? "" : tradesBlock(e)}
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
    const fromTrades = !!(e && e.trades && e.trades.length); // position comes from the trade list then
    for (const k of ["position", "qty", "avgCost", "since"]) {
      $(`f-${k}`).disabled = fromTrades;
      $(`f-${k}`).title = fromTrades ? "Set by this asset's trades" : "";
    }
    $("formTitle").textContent = e ? `Edit ${e.symbol}` : "Add an index or stock";
    $("cancelEdit").hidden = !e;
    if (e) $("entryForm").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("entryForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const entry = {};
    for (const k of FIELDS) {
      const v = $(`f-${k}`).value.trim();
      entry[k] = NUMERIC.includes(k) ? (v === "" ? null : Number(v)) : k === "since" ? v || null : v;
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

  async function alertAction(url, okText) {
    try {
      const r = await api(url, { method: "POST" });
      $("alertStatus").textContent = r.ok === false ? `Problem: ${r.error}` : typeof r.sent === "number" ? `Checked: ${r.sent} new item(s) sent.` : okText;
    } catch (err) {
      $("alertStatus").innerHTML = `<span class="down">${escapeHtml(err.message)}</span>`;
    }
  }
  $("shariahSave").addEventListener("click", () => {
    const text = $("shariahText").value.trim();
    if (!text) { $("shariahStatus").textContent = "Paste the list first."; return; }
    saveShariah(text);
  });
  $("shariahClear").addEventListener("click", () => {
    if (confirm("Remove the saved EGX 33 Shariah list? Your own tags are kept.")) saveShariah("");
  });
  $("shariahGroup").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const sym = btn.dataset.symbol;
    try {
      if (btn.dataset.act === "unlist") {
        const rest = state.shariahList.symbols.map((x) => x.symbol).filter((x) => x !== sym);
        if (!rest.length) return saveShariah("");
        await saveShariah(rest.join(", "));
      } else {
        await refreshShariah(await api("/api/shariah/tag", { method: "PUT", body: JSON.stringify({ symbol: sym, status: null }) }));
        load();
      }
    } catch (err) {
      $("shariahStatus").innerHTML = `<span class="down">${escapeHtml(err.message)}</span>`;
    }
  });
  $("alertTest").addEventListener("click", () => alertAction("/api/alerts/test", "Test message sent ✓ Check Telegram."));
  $("alertCheck").addEventListener("click", () => alertAction("/api/alerts/check"));

  $("settingsForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    const TEXT = ["currency", "telegramChatId", "backupDir"];
    for (const k of SETTINGS) {
      const v = $(`s-${k}`).value.trim();
      body[k] = TEXT.includes(k) ? v : v === "" ? null : Number(v);
      if (body[k] == null && k !== "capital") delete body[k]; // empty = keep the default
    }
    body.currency = body.currency || "EGP";
    body.alertsOn = $("s-alertsOn").checked;
    body.telegramToken = $("s-telegramToken").value.trim(); // empty keeps the saved token
    body.holidays = $("s-holidays").value.split(/[,\s]+/).map((d) => d.trim()).filter(Boolean);
    try {
      await api("/api/profile/settings", { method: "PUT", body: JSON.stringify(body) });
      $("settingsSaved").textContent = "Saved ✓";
      setTimeout(() => ($("settingsSaved").textContent = ""), 2000);
      await load();
      refreshAlertStatus();
      refreshBackups();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
  });

  $("cards").addEventListener("submit", async (ev) => {
    const form = ev.target.closest("form.trade-form");
    if (!form) return;
    ev.preventDefault();
    const sym = form.closest(".card").dataset.symbol;
    const d = Object.fromEntries(new FormData(form));
    const trade = { date: d.date, side: d.side, qty: Number(d.qty), price: Number(d.price), fees: d.fees === "" ? null : Number(d.fees) };
    const btn = form.querySelector("button");
    btn.disabled = true;
    try {
      await api(`/api/profile/entries/${encodeURIComponent(sym)}/trades`, { method: "POST", body: JSON.stringify(trade) });
      await load();
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
      btn.disabled = false;
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
    if (btn.dataset.act === "copy") {
      try { await navigator.clipboard.writeText(btn.dataset.text); btn.textContent = "Copied ✓"; } catch (_) { btn.textContent = "Select & copy"; }
      setTimeout(() => (btn.textContent = "Copy"), 1500);
      return;
    }
    if (btn.dataset.act === "del-trade") {
      const sym = btn.closest(".card").dataset.symbol;
      if (!confirm("Delete this trade?")) return;
      try {
        await api(`/api/profile/entries/${encodeURIComponent(sym)}/trades/${btn.dataset.i}`, { method: "DELETE" });
        await load();
      } catch (err) { setStatus(escapeHtml(err.message), true); }
      return;
    }
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
  $("backupRestore").addEventListener("click", async () => {
    const name = $("backupList").value;
    if (!name || !confirm(`Replace your profile with the copy "${name}"? Your current profile is kept as a copy first.`)) return;
    try {
      await api(`/api/backups/restore?${new URLSearchParams({ name })}`, { method: "POST" });
      $("backupStatus").textContent = "Restored ✓";
      await load();
      refreshBackups();
    } catch (err) {
      $("backupStatus").innerHTML = `<span class="down">${escapeHtml(err.message)}</span>`;
    }
  });

  load();
  refreshShariah();
  refreshAlertStatus();
  refreshBackups();
  refreshPhone();
})();
