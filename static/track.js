(() => {
  const $ = (id) => document.getElementById(id);
  const state = { result: null, sortKey: "bullEdge", sortDir: -1, poll: null };
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, signed = true) => (v == null ? "—" : `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const share = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
  const cls = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const BIAS_CLS = { Bullish: "bullish", "Mildly bullish": "bullish", Neutral: "", "Mildly bearish": "bearish", Bearish: "bearish" };
  const EDGE_PP = 2; // colour threshold for the detailed table
  const MIN_SIGNALS = 30, MIN_EDGE = 3;
  // +1 / -1 when a hit rate beats / trails the base rate by more than luck explains, else 0.
  function significance(rate, base, n) {
    if (rate == null || base == null || !n || n < MIN_SIGNALS) return 0;
    const p = base / 100, se = Math.sqrt((p * (1 - p)) / n) * 100, edge = rate - base;
    return edge >= MIN_EDGE && edge >= 2 * se ? 1 : edge <= -MIN_EDGE && edge <= -2 * se ? -1 : 0;
  }

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.hidden = !msg;
    el.innerHTML = msg || "";
    el.classList.toggle("error", isError);
  }

  const params = () => new URLSearchParams({ universe: $("universe").value, interval: $("interval").value, range: $("range").value });

  async function loadUniverses() {
    const u = await (await fetch("/api/universes")).json();
    $("universe").innerHTML = Object.entries(u).map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)} (${v.count})</option>`).join("");
    try {
      const saved = JSON.parse(localStorage.getItem("track") || "{}");
      for (const k of ["universe", "interval", "range"]) if (saved[k]) $(k).value = saved[k];
    } catch (_) {}
  }

  async function load() {
    try { localStorage.setItem("track", JSON.stringify({ universe: $("universe").value, interval: $("interval").value, range: $("range").value })); } catch (_) {}
    clearTimeout(state.poll);
    try {
      const res = await fetch(`/api/backtest?${params()}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      const running = body.status.running;
      $("run").disabled = running;
      $("run").textContent = body.result ? "Re-run" : "Run";
      state.result = body.result;
      render();
      if (running) {
        setStatus("Replaying the outlook over history… this takes about a minute for 30+ symbols.");
        state.poll = setTimeout(load, 3000);
      } else if (body.status.error) {
        setStatus(`The last run failed: ${escapeHtml(body.status.error)}`, true);
      } else if (body.result && body.result.rangeMissing) {
        setStatus("This saved result was made before periods existed, so it only has <b>All history</b>. Press <b>Re-run</b> to get the 6 month to 5 year views.");
      } else {
        setStatus(body.result ? "" : "No track record yet for this watchlist. Press <b>Run</b> to replay the outlook over its history (about a minute).");
      }
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
  }

  async function run() {
    $("run").disabled = true;
    try {
      const res = await fetch(`/api/backtest/run?${params()}`, { method: "POST" });
      if (!res.ok) throw new Error((await res.json()).detail || `Request failed (${res.status})`);
    } catch (err) {
      setStatus(escapeHtml(err.message), true);
    }
    load();
  }

  // Pool several buckets: weighted by signal count.
  function pool(buckets) {
    const n = buckets.reduce((a, b) => a + b.n, 0);
    const w = (k) => (n ? buckets.reduce((a, b) => a + (b[k] ?? 0) * b.n, 0) / n : null);
    const hasNet = buckets.some((b) => b.avgRNet != null); // results saved before costs were added have none
    return { n, asExpected: w("asExpected"), avgRet: w("avgRet"), targetFirst: w("targetFirst"), stopFirst: w("stopFirst"),
             avgR: w("avgR"), avgRNet: hasNet ? w("avgRNet") : null };
  }

  function verdict(o) {
    const bull = pool(o.buckets.filter((b) => BIAS_CLS[b.bias] === "bullish"));
    const bear = pool(o.buckets.filter((b) => BIAS_CLS[b.bias] === "bearish"));
    const baseUp = o.upShare, baseDown = 100 - o.upShare;
    const line = (label, p, base, word) => {
      if (!p.n) return "";
      const edge = p.asExpected - base;
      const sig = significance(p.asExpected, base, p.n);
      const tone = sig > 0 ? "good" : sig < 0 ? "bad" : "flat";
      const says = tone === "good" ? `a real but modest edge (+${edge.toFixed(1)} pts)` : tone === "bad" ? `worse than chance (${edge.toFixed(1)} pts)`
        : Math.abs(edge) < MIN_EDGE ? "no reliable edge" : `no reliable edge (a ${edge > 0 ? "+" : ""}${edge.toFixed(1)} pt gap is within luck for ${p.n.toLocaleString()} signals)`;
      return `<li class="${tone}"><b>${label} signals:</b> price ${word} after ${share(p.asExpected)} of them, vs ${share(base)} of all weeks, so <b>${says}</b>. `
        + `The trade plan hit its target first ${share(p.targetFirst)} of the time and its stop first ${share(p.stopFirst)}, averaging ${p.avgR >= 0 ? "+" : ""}${p.avgR.toFixed(2)} R per trade`
        + (p.avgRNet != null ? `, or <b class="${p.avgRNet > 0 ? "up" : "down"}">${p.avgRNet >= 0 ? "+" : ""}${p.avgRNet.toFixed(2)} R after costs</b>` : "") + `.</li>`;
    };
    return `<h2>Verdict</h2><ul>${line("Bullish", bull, baseUp, "rose")}${line("Bearish", bear, baseDown, "fell")}</ul>
      <p class="muted small">"R" is the result in units of the risk taken (entry to stop): +1 R means you made what you risked, −1 R means the stop was hit.</p>`;
  }

  function symbolRows(r) {
    return Object.entries(r.perSymbol).map(([sym, s]) => {
      const bull = pool(s.buckets.filter((b) => BIAS_CLS[b.bias] === "bullish"));
      const bear = pool(s.buckets.filter((b) => BIAS_CLS[b.bias] === "bearish"));
      return { symbol: sym, query: s.query, name: s.name, n: s.n, bullN: bull.n, bullUp: bull.asExpected, bullAvg: bull.avgRet,
               bullEdge: bull.n && s.upShare != null ? bull.asExpected - s.upShare : null,
               bearN: bear.n, bearDown: bear.asExpected, baseUp: s.upShare };
    });
  }

  // Plain verdict on a symbol's bullish signals (see significance(): a few lucky signals don't count).
  const VERDICTS = {
    useful: { label: "Useful", order: 0 },
    misleading: { label: "Misleading", order: 1 },
    unclear: { label: "Unclear", order: 2 },
    none: { label: "No edge", order: 3 },
    few: { label: "Not enough data", order: 4 },
  };

  function symbolVerdict(s) {
    if (!s.bullN || s.bullN < MIN_SIGNALS || s.baseUp == null) {
      const n = s.bullN || 0;
      return { key: "few", text: `Only ${n} bullish signal${n === 1 ? "" : "s"} in this period, too few to judge.` };
    }
    const edge = s.bullEdge;
    const sig = significance(s.bullUp, s.baseUp, s.bullN);
    const said = `When the chart said bullish, the price rose ${s.bullUp.toFixed(0)}% of the time`;
    const normal = `a normal week (${s.baseUp.toFixed(0)}%)`;
    if (sig > 0) return { key: "useful", text: `${said}, clearly more than ${normal}. Worth paying attention to.` };
    if (sig < 0) return { key: "misleading", text: `${said}, less than ${normal}. Don't buy on bullish signals here.` };
    if (Math.abs(edge) >= MIN_EDGE) {
      return { key: "unclear", text: `${said}, vs ${normal}. That gap could still be luck with only ${s.bullN} signals, so don't rely on it either way.` };
    }
    return { key: "none", text: `${said}, about the same as ${normal}. The signal doesn't help here.` };
  }

  function renderSimple(rows) {
    const list = rows.map((s) => ({ ...s, v: symbolVerdict(s) }));
    list.sort((a, b) => VERDICTS[a.v.key].order - VERDICTS[b.v.key].order || Math.abs(b.bullEdge ?? 0) - Math.abs(a.bullEdge ?? 0));
    const allFew = list.length && list.every((s) => s.v.key === "few");
    $("fewHint").hidden = !allFew;
    $("simple").querySelector("tbody").innerHTML = list.map((s) => `
      <tr data-symbol="${escapeHtml(s.query)}">
        <td><div class="sym">${escapeHtml(s.query)}</div><div class="muted small">${escapeHtml(s.name)}</div></td>
        <td><span class="verdict-pill ${s.v.key}">${VERDICTS[s.v.key].label}</span><div class="plain">${escapeHtml(s.v.text)}</div></td>
      </tr>`).join("");
  }

  function setDetails(show) {
    $("detailsWrap").hidden = !show;
    $("simpleWrap").hidden = show;
    $("toggleDetails").textContent = show ? "Show simple view" : "Show details";
    try { localStorage.setItem("trackDetails", show ? "1" : ""); } catch (_) {}
  }

  function render() {
    const r = state.result;
    $("report").hidden = !r;
    if (!r) { $("meta").textContent = ""; return; }
    const o = r.overall;
    const since = r.since || Object.values(r.perSymbol).map((s) => s.from).sort()[0];
    $("meta").textContent = `${r.symbols} symbols · ${o.n.toLocaleString()} signals since ${since} · run ${new Date(r.generatedAt).toLocaleString()} (${r.seconds}s)`
      + (r.feePct != null ? ` · costs ${r.feePct}% per trade` : " · made before costs were counted: Re-run to include them")
      + (r.minAdtv ? ` · only when the stock traded ${(r.minAdtv / 1e6).toLocaleString()}M+ a day` : "")
      + (r.skipped.length ? ` · skipped: ${r.skipped.join(", ")}` : "");
    $("win").textContent = r.window;
    $("horizonNote").textContent = `price change over the ${r.horizon}`;
    $("verdict").innerHTML = verdict(o);

    const row = (b) => `
      <tr class="${b.small ? "small-n" : ""}">
        <td><span class="bias ${BIAS_CLS[b.bias]}">${escapeHtml(b.bias)}</span>${b.small && b.n ? ' <span class="muted small" title="Fewer than 30 signals">few</span>' : ""}</td>
        <td class="num">${b.n.toLocaleString()}</td>
        <td class="num ${cls(b.avgRet)}">${pct(b.avgRet)}</td>
        <td class="num ${cls(b.excess)}">${pct(b.excess)}</td>
        <td class="num">${share(b.asExpected)}</td>
        <td class="num">${share(b.targetFirst)}</td>
        <td class="num">${share(b.stopFirst)}</td>
        <td class="num ${cls(b.avgR)}">${b.avgR == null ? "—" : `${b.avgR > 0 ? "+" : ""}${b.avgR.toFixed(2)}`}</td>
        <td class="num ${cls(b.avgRNet)}">${b.avgRNet == null ? "—" : `${b.avgRNet > 0 ? "+" : ""}${b.avgRNet.toFixed(2)}`}</td>
      </tr>`;
    $("buckets").querySelector("tbody").innerHTML = o.buckets.map(row).join("") + `
      <tr class="baseline"><td>Any week (baseline)</td><td class="num">${o.n.toLocaleString()}</td><td class="num ${cls(o.baseline)}">${pct(o.baseline)}</td>
        <td class="num">—</td><td class="num" title="Share of all samples that rose">${share(o.upShare)} rose</td><td class="num">—</td><td class="num">—</td><td class="num">—</td><td class="num">—</td></tr>`;

    const rows = symbolRows(r);
    renderSimple(rows);
    const k = state.sortKey;
    rows.sort((a, b) => (a[k] == null ? 1 : b[k] == null ? -1 : (typeof a[k] === "string" ? a[k].localeCompare(b[k]) : a[k] - b[k]) * state.sortDir));
    document.querySelectorAll("#symbols th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.k === k);
      th.dataset.dir = th.dataset.k === k ? (state.sortDir > 0 ? "▲" : "▼") : "";
    });
    const edgeCls = (v, base, n) => {
      const sig = significance(v, base, n);
      if (sig) return sig > 0 ? "up" : "down";
      return v != null && base != null && Math.abs(v - base) >= MIN_EDGE ? "unproven" : "";
    };
    $("symbols").querySelector("tbody").innerHTML = rows.map((s) => `
      <tr data-symbol="${escapeHtml(s.query)}">
        <td><div class="sym">${escapeHtml(s.query)}</div><div class="muted small">${escapeHtml(s.name)}</div></td>
        <td class="num">${s.n}</td>
        <td class="num">${s.bullN}</td>
        <td class="num">${share(s.bullUp)}</td>
        <td class="num ${edgeCls(s.bullUp, s.baseUp, s.bullN)}" title="${s.bullN < MIN_SIGNALS ? "Fewer than 30 signals" : ""}">${s.bullEdge == null ? "—" : `${s.bullEdge > 0 ? "+" : ""}${s.bullEdge.toFixed(1)} pts`}</td>
        <td class="num ${cls(s.bullAvg)}">${pct(s.bullAvg)}</td>
        <td class="num">${s.bearN}</td>
        <td class="num ${edgeCls(s.bearDown, s.baseUp == null ? null : 100 - s.baseUp, s.bearN)}">${share(s.bearDown)}</td>
        <td class="num">${share(s.baseUp)}</td>
      </tr>`).join("");
  }

  $("run").addEventListener("click", run);
  $("universe").addEventListener("change", load);
  $("interval").addEventListener("change", load);
  $("range").addEventListener("change", load);
  document.querySelector("#symbols thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    state.sortDir = state.sortKey === th.dataset.k ? -state.sortDir : th.dataset.k === "symbol" ? 1 : -1;
    state.sortKey = th.dataset.k;
    render();
  });
  $("toggleDetails").addEventListener("click", () => setDetails($("detailsWrap").hidden));
  try { setDetails(!!localStorage.getItem("trackDetails")); } catch (_) {}
  document.querySelector("#report").addEventListener("click", (e) => {
    const tr = e.target.closest("#symbols tr[data-symbol], #simple tr[data-symbol]");
    if (tr) location.href = `/?${new URLSearchParams({ symbol: tr.dataset.symbol, interval: $("interval").value, period: $("interval").value === "1wk" ? "5y" : "1y" })}`;
  });

  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (_) {}
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });

  loadUniverses().then(load);
})();
