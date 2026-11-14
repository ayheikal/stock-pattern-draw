(() => {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], sortKey: null, sortDir: 1 };
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) >= 1 ? 2 : 5 }));
  const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const tone = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
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
      const saved = JSON.parse(localStorage.getItem("intraday") || "{}");
      for (const k of ["universe", "symbols", "show", "interval"]) if (saved[k] != null) $(k).value = saved[k];
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
    const params = { universe: $("universe").value, symbols: $("symbols").value.trim(), show: $("show").value, interval: $("interval").value, halal_only: $("halalOnly").checked };
    try { localStorage.setItem("intraday", JSON.stringify({ universe: params.universe, symbols: params.symbols, show: params.show, interval: params.interval, halalOnly: params.halal_only })); } catch (_) {}
    btn.disabled = true;
    setStatus("Scanning… this can take a few seconds per dozen symbols.");
    $("results").hidden = true;
    const t0 = performance.now();
    try {
      const res = await fetch(`/api/intraday?${new URLSearchParams(params)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      state.rows = body.results;
      state.sortKey = null;
      $("found").textContent = body.results.length;
      $("found").hidden = false;
      $("scanInfo").textContent = `${body.scanned} scanned in ${((performance.now() - t0) / 1000).toFixed(1)}s`
        + (body.skipped.length ? ` · skipped (no data): ${body.skipped.join(", ")}` : "");
      renderTable();
      if (body.logged) loadLog();
      const hidden = body.hidden ? `${body.hidden} with fewer rules passed hidden by <b>Show</b>` : "";
      setStatus(body.results.length
        ? (hidden ? `${hidden}.` : "")
        : `Nothing meets the rules right now${hidden ? `: ${hidden}` : ""}. Try <b>Show: 4 or more</b> or <b>Everything</b> to see what's close.`
          + (params.halal_only ? " <b>Halal only</b> needs the EGX 33 Shariah list in <a href=\"/profile\">My profile</a>, or stocks you tagged halal." : ""));
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    } finally {
      btn.disabled = false;
    }
  }

  const rules = (r) => r.checks.map((c) =>
    `<span class="rule ${c.ok == null ? "na" : c.ok ? "ok" : "no"}" title="${escapeHtml(c.text)}">${escapeHtml(c.label)}</span>`).join("");

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
        <td class="rules-cell"><b>${r.passed}/5</b> ${rules(r)}</td>
        <td>${r.entry ? `<span class="up">${r.entry === "breakout" ? "Breakout" : "Retest"}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num">${fmt(r.last)}</td>
        <td class="num ${tone(r.dayPct)}">${pct(r.dayPct)}</td>
        <td class="num ${tone(r.vwapPct)}">${pct(r.vwapPct)}</td>
        <td class="num ${r.rsi > 50 ? "up" : "down"}">${r.rsi == null ? "—" : r.rsi.toFixed(1)}</td>
        <td class="num ${r.rvol == null ? "" : r.rvol >= 1.5 ? "up" : ""}">${r.rvol == null ? "—" : `${r.rvol.toFixed(2)}×`}</td>
        <td class="num">${r.atrPct == null ? "—" : `${r.atrPct.toFixed(2)}%`}</td>
        <td class="num">${fmt(r.stop)}</td>
        <td class="num">${fmt(r.target)}</td>
        <td class="num">${r.adx == null ? "—" : r.adx.toFixed(0)}</td>
        <td class="nowrap muted">${escapeHtml(r.lastBar)}</td>
      </tr>`).join("");
    $("results").hidden = !rows.length;
  }

  $("scan").addEventListener("submit", (e) => { e.preventDefault(); scan(); });
  $("symbols").addEventListener("input", syncWatchlist);
  document.querySelector("#results thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    const k = th.dataset.k;
    state.sortDir = state.sortKey === k ? -state.sortDir : (["symbol", "entry"].includes(k) ? 1 : -1);
    state.sortKey = k;
    renderTable();
  });
  document.querySelector("#results tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    // The chart page has no 5m / 15m bars, so open the hourly chart; a new tab keeps the scan here.
    if (tr) window.open(`/?${new URLSearchParams({ symbol: tr.dataset.symbol, interval: "1h", period: "3mo" })}`, "_blank", "noopener");
  });

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  // ---- Do these rules make money? (app/intraday_backtest.py) ----
  const share = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
  const signed = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`);
  const VERDICT = { profit: "Profit", loss: "Loss", unclear: "Unclear", few: "Too few trades" };
  const RULE_LABEL = { trend: "Trend", vwap: "VWAP", rsi: "RSI", volume: "Volume", entry: "Entry" };
  const pill = (key, label) => `<span class="verdict-pill ${key}">${escapeHtml(label)}</span>`;
  const bt = { poll: null };
  const btParams = () => new URLSearchParams({ universe: $("universe").value, interval: $("interval").value });

  function btStatus(msg, isError = false) {
    const el = $("btStatus");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  async function loadBacktest() {
    clearTimeout(bt.poll);
    if (!$("universe").value) return;
    try {
      const res = await fetch(`/api/intraday/backtest?${btParams()}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      $("btRun").disabled = body.status.running;
      $("btRun").textContent = body.result ? "Re-run backtest" : "Run backtest";
      renderBacktest(body.result);
      if (body.status.running) {
        btStatus("Replaying the rules over history… about half a minute for 30 stocks.");
        bt.poll = setTimeout(loadBacktest, 3000);
      } else if (body.status.error) btStatus(`The last run failed: ${escapeHtml(body.status.error)}`, true);
      else btStatus(body.result ? "" : "No backtest yet for this watchlist and bar size. Press <b>Run backtest</b>.");
    } catch (err) {
      btStatus(escapeHtml(err.message), true);
    }
  }

  async function runBacktest() {
    $("btRun").disabled = true;
    try {
      const res = await fetch(`/api/intraday/backtest/run?${btParams()}`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail || `Request failed (${res.status})`);
    } catch (err) {
      btStatus(escapeHtml(err.message), true);
    }
    loadBacktest();
  }

  // What the numbers say, in plain words: [tone, html] per finding.
  function backtestFindings(r) {
    const by = Object.fromEntries(r.strategies.map((s) => [s.key, s]));
    const a = by.all5, any = by.any, out = [];
    if (!a.n) return [["flat", "The five rules never all passed in this history, so there is nothing to judge."]];
    const trades = `${a.n.toLocaleString()} trades`;
    if (a.verdict === "few") out.push(["flat", `<b>Too few trades to judge:</b> only ${a.n} (at least 30 are needed). So far ${signed(a.avgNet)}% a trade after costs.`]);
    else if (a.verdict === "profit") out.push(["good", `<b>Profitable in this history:</b> ${signed(a.avgNet)}% a trade after costs, over ${trades} (${share(a.win)} won).`]);
    else if (a.verdict === "loss") out.push(["bad", `<b>Loses money after costs:</b> ${signed(a.avgNet)}% a trade over ${trades} (${share(a.win)} won). `
      + (a.avgRet > 0 ? `Before costs it made ${signed(a.avgRet)}% a trade, far less than the ${r.costPct.toFixed(2)}% it costs to buy and sell.`
        : `It lost before costs too (${signed(a.avgRet)}% a trade).`)]);
    else out.push(["flat", `<b>No clear result:</b> ${signed(a.avgNet)}% a trade after costs over ${trades}, which luck alone could explain.`]);
    if (a.avgRisk) out.push(["flat", `<b>Why costs weigh so much here:</b> the stop sits about ${a.avgRisk.toFixed(2)}% from the entry and costs are ${r.costPct.toFixed(2)}%, `
      + `so each trade starts ${(r.costPct / a.avgRisk).toFixed(1)}× its own risk behind. Bigger bars (a wider ATR) or a lower fee shrink that.`]);
    if (any.n && a.avgRet != null && any.avgRet != null) {
      const better = a.avgRet > any.avgRet;
      out.push([better ? "good" : "bad", `<b>Rules vs no rules:</b> ${signed(a.avgRet)}% a trade before costs with the five rules, ${signed(any.avgRet)}% buying every bar blindly. `
        + (better ? "The rules do pick better moments." : "The rules pick no better than chance.")]);
    }
    const idle = Object.entries(RULE_LABEL).filter(([k]) => by[`no_${k}`] && by[`no_${k}`].n >= 30 && by[`no_${k}`].avgRet >= a.avgRet).map(([, l]) => l);
    if (idle.length) out.push(["flat", `<b>Rules that added nothing here:</b> ${idle.join(", ")}. Dropping ${idle.length > 1 ? "any one of them" : "it"} gave the same result or better.`]);
    if (a.firstHalf != null && a.secondHalf != null && a.n >= 60)
      out.push(["flat", `<b>Steady or not:</b> ${signed(a.firstHalf)}% a trade after costs in the first half of the period, ${signed(a.secondHalf)}% in the second.`]);
    return out;
  }

  const statRow = (s, cls = "") => `<tr class="${cls}">
      <td>${escapeHtml(s.label)}</td>
      <td class="num">${s.n.toLocaleString()}</td>
      <td class="num">${share(s.win)}</td>
      <td class="num">${share(s.target)}</td>
      <td class="num">${share(s.stop)}</td>
      <td class="num">${share(s.time)}</td>
      <td class="num ${tone(s.avgR)}">${signed(s.avgR)}</td>
      <td class="num ${tone(s.avgRet)}">${s.avgRet == null ? "—" : `${signed(s.avgRet)}%`}</td>
      <td class="num ${tone(s.avgNet)}"><b>${s.avgNet == null ? "—" : `${signed(s.avgNet)}%`}</b></td>
      <td class="num">${s.profitFactor == null ? "—" : s.profitFactor.toFixed(2)}</td>
      <td>${s.n ? pill(s.verdict, VERDICT[s.verdict]) : '<span class="muted">—</span>'}</td>
    </tr>`;

  function renderBacktest(r) {
    $("btTable").hidden = $("btVerdict").hidden = !r;
    $("btNote").textContent = r ? `${r.symbols} stocks · ${r.from} to ${r.to} · ${r.bars.toLocaleString()} bars · run ${new Date(r.generatedAt).toLocaleDateString()}` : "";
    if (!r) return;
    $("btVerdict").innerHTML = `<ul>${backtestFindings(r).map(([c, t]) => `<li class="${c}">${t}</li>`).join("")}</ul>
      <p class="muted small">Short history (all the data sources keep of small bars), today's stocks only, and quotes that may be late: a first check, not proof.
      ${r.skipped.length ? `Left out: ${escapeHtml(r.skipped.join(", "))}.` : ""}</p>`;
    $("btTable").querySelector("tbody").innerHTML = r.strategies.map((s) => statRow(s, s.key === "any" ? "baseline" : "")).join("");
  }

  // ---- Live record (app/intraday_log.py) ----
  const RESULT = { open: "Open", target: "Target", stop: "Stop", time: "Timed out", skipped: "No entry", expired: "Not scored" };
  const RESULT_TIP = { skipped: "The signal came on the session's last bar, so there was no next bar to buy", expired: "Its bars are no longer in the data, so it couldn't be scored" };

  async function loadLog(method = "GET", path = "/api/intraday/log", body) {
    try {
      const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body && JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
      if (path.endsWith("/auto")) return loadLog();
      renderLog(data);
    } catch (err) {
      if (path.endsWith("/auto")) await loadLog();  // put the switch back where the server has it
      $("autoNote").textContent = err.message;
    }
  }

  function renderLog(d) {
    const all = d.summary.find((s) => s.key === "all");
    $("autoOn").checked = d.auto.on;
    $("autoNote").textContent = d.auto.on ? `On: ${d.auto.universe}, ${d.auto.interval} bars${d.lastAuto ? ` · last scan ${new Date(d.lastAuto).toLocaleString()}` : ""}` : "";
    $("logNote").textContent = d.total ? `${d.total} logged · ${all.n} settled · ${d.open} open` : "";
    let items;
    if (!d.total) items = [["flat", "Nothing logged yet. Run a scan during market hours, or switch on the automatic scan: each 5/5 setup is recorded here and scored once its session ends."]];
    else if (!all.n) items = [["flat", `${d.total} signal${d.total > 1 ? "s" : ""} logged, none settled yet. Outcomes fill in as new bars arrive.`]];
    else {
      const head = all.verdict === "few" ? ["flat", `<b>Too early to judge:</b> ${all.n} settled signal${all.n > 1 ? "s" : ""} (at least 30 are needed).`]
        : all.verdict === "profit" ? ["good", "<b>Working so far:</b>"] : all.verdict === "loss" ? ["bad", "<b>Losing so far:</b>"] : ["flat", "<b>No clear result yet:</b>"];
      items = [[head[0], `${head[1]} ${signed(all.avgNet)}% a trade after costs (${signed(all.avgRet)}% before), ${share(all.win)} won, `
        + `target first ${share(all.target)}, stop first ${share(all.stop)}, avg ${signed(all.avgR)} R.`]];
      if (d.summary.length > 2) for (const s of d.summary.filter((x) => x.key !== "all"))
        items.push(["flat", `<b>${escapeHtml(s.label)} bars:</b> ${s.n} settled, ${signed(s.avgNet)}% a trade after costs, ${share(s.win)} won.`]);
    }
    $("logVerdict").innerHTML = `<ul>${items.map(([c, t]) => `<li class="${c}">${t}</li>`).join("")}</ul>`;
    $("logTable").hidden = !d.signals.length;
    $("logTable").querySelector("tbody").innerHTML = d.signals.map((s) => {
      const net = s.retPct == null ? null : s.retPct - d.costPct;
      return `<tr>
        <td class="nowrap">${escapeHtml(s.lastBar)}</td>
        <td><div class="sym">${escapeHtml(s.symbol)}</div><div class="muted small">${escapeHtml(s.name)}</div></td>
        <td>${escapeHtml(s.interval)}</td>
        <td class="muted">${escapeHtml(s.source)}</td>
        <td class="num">${fmt(s.entry)}</td>
        <td class="num">${fmt(s.stop)}</td>
        <td class="num">${fmt(s.target)}</td>
        <td class="num">${fmt(s.exit)}</td>
        <td title="${escapeHtml(RESULT_TIP[s.status] || s.exitTime || "")}">${pill(s.status, RESULT[s.status] || s.status)}</td>
        <td class="num ${tone(s.R)}">${signed(s.R)}</td>
        <td class="num ${tone(net)}">${net == null ? "—" : `${signed(net)}%`}</td>
      </tr>`;
    }).join("");
  }

  $("btRun").addEventListener("click", runBacktest);
  $("universe").addEventListener("change", loadBacktest);
  $("interval").addEventListener("change", loadBacktest);
  $("logScore").addEventListener("click", async () => {
    $("logScore").disabled = true;
    await loadLog("POST", "/api/intraday/log/score");
    $("logScore").disabled = false;
  });
  $("autoOn").addEventListener("change", () =>
    loadLog("PUT", "/api/intraday/log/auto", { on: $("autoOn").checked, universe: $("universe").value, interval: $("interval").value }));

  loadUniverses().then(loadBacktest);
  loadLog();
})();
