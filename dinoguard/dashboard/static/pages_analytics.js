/* Compare sessions / groups, and the analytics blocks on the Metrics page (CLAUDE.md §17.2-3). */
const CMP_METRICS = ["duration_s", "tool_calls", "denies", "instructs", "errors", "cost_usd", "tokens"];
const fmtV = x => (typeof x === "number" ? (Number.isInteger(x) ? fmtNum(x) : x.toFixed(3)) : esc(x ?? "—"));

DG.pages.compare = async (v, [a, b]) => {
  v.innerHTML = `<h1>Compare</h1>
  <form class="filters" id="two"><input name="a" placeholder="session A" value="${esc(a || "")}"><input name="b" placeholder="session B" value="${esc(b || "")}"><button>Compare two sessions</button></form>
  <form class="filters" id="grp"><select name="by"><option>model</option><option>harness</option><option>environment</option><option>outcome</option></select>
    <input name="days" type="number" value="30" style="width:5em"> days <button>Compare groups</button></form><div id="out"></div>`;
  const out = v.querySelector("#out");
  v.querySelector("#two").onsubmit = e => { e.preventDefault(); const f = new FormData(e.target);
    location.hash = "#/compare/" + encodeURIComponent(f.get("a")) + "/" + encodeURIComponent(f.get("b")); };
  v.querySelector("#grp").onsubmit = async e => { e.preventDefault(); const f = new FormData(e.target);
    const r = await api(`/compare?by=${encodeURIComponent(f.get("by"))}&days=${encodeURIComponent(f.get("days"))}`);
    const metrics = ["sessions", "clean_rate", ...CMP_METRICS.map(k => "avg_" + k)];
    out.innerHTML = `<h2>Sessions by ${esc(r.by)}</h2>` + table([{ label: "metric", key: "m" },
      ...r.groups.map((g, i) => ({ label: g.group, html: row => row.vals[i] }))],
      metrics.map(m => ({ m: m.replace("avg_", "avg "), vals: r.groups.map(g => fmtV(g[m])) })), { empty: "No sessions." }); };
  if (!(a && b)) return;
  try {
    const r = await api(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    const evals = [...new Set([...Object.keys(r.a.evals), ...Object.keys(r.b.evals)])];
    const rows = [["harness", r.a.harness, r.b.harness, ""], ["model", r.a.model, r.b.model, ""],
      ["environment", r.a.environment, r.b.environment, ""],
      ...CMP_METRICS.map(k => [k, r.a[k], r.b[k], r.delta[k]]),
      ...evals.map(n => ["eval " + n, r.a.evals[n], r.b.evals[n], ""])];
    out.innerHTML = `<h2>Side by side</h2>` + table([{ label: "metric", get: x => x[0] },
      { label: "A " + r.a.id.slice(0, 12), html: x => fmtV(x[1]) }, { label: "B " + r.b.id.slice(0, 12), html: x => fmtV(x[2]) },
      { label: "B − A", html: x => fmtV(x[3]) }], rows) +
      `<h2>Tool calls</h2>` + table([{ label: "tool", key: "tool" }, { label: "A", key: "a" }, { label: "B", key: "b" }], r.tools) +
      `<p class="small"><a href="#/trace/${encodeURIComponent(r.a.id)}">open A</a> · <a href="#/trace/${encodeURIComponent(r.b.id)}">open B</a></p>`;
  } catch (e) { out.innerHTML = `<pre class="box">${esc(e.message)}</pre>`; }
};

async function metricsAnalytics(el) {
  const [errs, tools, pols] = await Promise.all([api("/errors?days=7"), api("/analytics/tools?days=7"), api("/analytics/policies?days=7")]);
  el.innerHTML = `<h2>Tool analytics</h2>` + table([{ label: "tool", key: "tool" }, { label: "calls", key: "calls" },
      { label: "denied", key: "denied" }, { label: "errors", key: "errors" }, { label: "error rate", get: r => (r.error_rate * 100).toFixed(1) + "%" },
      { label: "p50 ms", get: r => r.p50_ms ?? "—" }, { label: "p95 ms", get: r => r.p95_ms ?? "—" }], tools, { empty: "No tool calls yet." }) +
    `<h2>Policy analytics</h2>` + table([{ label: "policy", key: "policy" }, { label: "deny", key: "deny" }, { label: "instruct", key: "instruct" },
      { label: "would deny (observe)", key: "would_deny" }, { label: "would instruct", key: "would_instruct" }, { label: "errors", key: "errors" }], pols,
      { empty: "No policy decisions yet." }) +
    `<h2>Error groups</h2>` + table([{ label: "count", key: "count" }, { label: "tools", get: r => Object.keys(r.tools).join(", ") },
      { label: "sessions", get: r => r.sessions.length }, { label: "last seen", get: r => fmtTs(r.last_seen) },
      { label: "pattern", html: r => `<span class="mono small">${esc(r.pattern)}</span>` }], errs, { empty: "No tool errors in the last 7 days." });
}
