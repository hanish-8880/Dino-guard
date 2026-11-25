/* Audit, Findings & insights, Evaluations, Alerts, Settings */
DG.pages.audit = async (v) => {
  const o = await api("/audit/overview");
  v.innerHTML = `<h1>Audit</h1><div class="note small">Replays local agent history through the built-in policies. Findings are <b>evidence for review, not proof</b>.</div>
  <div class="row"><button id="run" class="primary">Run audit now (30 days)</button><span id="st" class="muted small"></span></div>
  <div class="grid">${tile("Sessions", fmtNum(o.sessions))}${tile("Tool calls", fmtNum(o.tool_calls))}${tile("Projects", fmtNum(o.projects.length))}${tile("Policy hits", fmtNum(o.policy_hits))}</div>
  <div class="cols"><div id="proj"></div><div id="pol"></div></div>
  <h2>Frequent findings</h2><div id="find"></div>`;
  hBars(v.querySelector("#proj"), o.projects.slice(0, 10).map(p => ({ label: p.project, value: p.hits })), { title: "Policy hits by project" });
  hBars(v.querySelector("#pol"), o.by_policy.slice(0, 10).map(p => ({ label: p.policy, value: p.n })), { title: "Hits by policy", color: "var(--series-2)" });
  const showFindings = (rows) => { const el = v.querySelector("#find");
    el.innerHTML = table([{ label: "severity", html: r => badge(r.severity === "high" ? "deny" : r.severity === "medium" ? "instruct" : "observe", r.severity) },
      { label: "failure mode", key: "failure_mode" }, { label: "count", key: "count" }, { label: "sessions", get: r => r.session_ids.length },
      { label: "example", get: r => (r.evidence[0] || {}).summary || "" },
      { label: "", html: r => `<button data-f="${r.id}">Draft a policy</button>` }], rows, { empty: "No findings yet. Run an audit." });
    el.querySelectorAll("[data-f]").forEach(b => b.onclick = async () => { const d = await post(`/findings/${b.dataset.f}/draft`);
      alert(`Drafted ${d.name} (${d.method}) at ${d.path}.\nTest it: dinoguard policy test ${d.name}`); }); };
  showFindings(await api("/findings"));
  v.querySelector("#run").onclick = async () => { v.querySelector("#st").textContent = "running…";
    const r = await post("/audit/run", { days: 30 }); v.querySelector("#st").textContent = `scanned ${r.sessions} sessions, ${r.tool_calls} tool calls`;
    showFindings(await api("/findings")); };
};

DG.pages.insights = async (v) => {
  const rows = await api("/insights");
  v.innerHTML = `<h1>Findings &amp; insights</h1><div class="filters"><select id="st"><option value="">all</option><option>open</option><option>ack</option><option>assigned</option><option>resolved</option></select></div><div id="list"></div><div id="detail"></div>`;
  const render = () => { const st = v.querySelector("#st").value; const r = rows.filter(x => !st || x.status === st); const el = v.querySelector("#list");
    el.innerHTML = table([{ label: "created", get: x => fmtTs(x.created_at) }, { label: "source", key: "source" }, { label: "title", key: "title" },
      { label: "status", key: "status" }, { label: "owner", get: x => x.owner || "—" }, { label: "comments", get: x => x.comments.length }], r, { onRow: true, empty: "No insights. Alerts and audits open them." });
    bindRows(el, r, detail); };
  function detail(x) { const el = v.querySelector("#detail");
    el.innerHTML = `<h2>${esc(x.title)}</h2><div class="row">${["ack", "resolved", "open"].map(s => `<button data-s="${s}">${s}</button>`).join("")}
      <input id="owner" placeholder="assign to" value="${esc(x.owner || "")}"><button id="assign">Assign</button></div>
      ${x.comments.map(c => `<div class="card small"><span class="muted">${fmtTs(c.ts)}</span> ${esc(c.text)}</div>`).join("")}
      <div class="row"><input id="cm" style="flex:1" placeholder="comment"><button id="addc">Comment</button></div>`;
    const upd = async (body) => { await post("/insights/" + x.id, body); location.reload(); };
    el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => upd({ status: b.dataset.s }));
    el.querySelector("#assign").onclick = () => upd({ owner: el.querySelector("#owner").value });
    el.querySelector("#addc").onclick = () => upd({ comment: el.querySelector("#cm").value }); }
  v.querySelector("#st").addEventListener("change", render); render();
};

DG.pages.evals = async (v) => {
  const evs = await api("/evals");
  v.innerHTML = `<h1>Evaluations</h1><div class="note small">Evals score finished sessions. Create and test with <span class="mono">dinoguard eval new|test|deploy|backfill</span>; deployed versions score new sessions as they end.</div>
  ${table([{ label: "name", key: "name" }, { label: "kind", key: "kind" }, { label: "version", key: "version" }, { label: "state", html: e => e.enabled ? badge("allow", "deployed") : badge("observe", "draft") },
    { label: "spec", get: e => (e.spec || "").slice(0, 80) }], evs, { onRow: true, empty: "No evals yet. The built-in sentiment eval appears after it is deployed." })}<div id="chart"></div><div id="res"></div>`;
  bindRows(v, evs, async e => { const r = await api(`/evals/${encodeURIComponent(e.name)}/results`);
    line(v.querySelector("#chart"), r.slice().reverse().map(x => ({ x: fmtTs(x.created_at), y: +(x.score ?? 0) })), { title: `${e.name} score over time`, max: 1, label: "score" });
    v.querySelector("#res").innerHTML = table([{ label: "time", get: x => fmtTs(x.created_at) }, { label: "session", html: x => `<a href="#/trace/${encodeURIComponent(x.session_id)}">${esc(x.session_id.slice(0, 12))}</a>` },
      { label: "v", key: "version" }, { label: "score", key: "score" }, { label: "passed", get: x => x.passed ? "yes" : "no" }, { label: "reasoning", get: x => (x.reasoning || "").slice(0, 120) }], r); });
};

DG.pages.alerts = async (v) => {
  const al = await api("/alerts");
  v.innerHTML = `<h1>Alerts</h1><div class="card"><b>Tell me when …</b><div class="row" style="margin-top:8px"><input id="t" style="flex:1" placeholder="denies > 5 in 10 minutes"><select id="sev"><option>warning</option><option>critical</option></select><input id="ch" placeholder="channels (comma)"><button id="prev">Preview</button><button id="add" class="primary">Create</button></div>
  <div id="pv" class="muted small">Try: "errors > 3 in 15 minutes", "cost > 5 in 1 day", "sentiment score < 0.4", "sql: SELECT COUNT(*) FROM events WHERE decision='deny' > 10".</div></div>
  <h2>Alerts</h2>${table([{ label: "name", key: "name" }, { label: "kind", key: "trigger_kind" }, { label: "severity", html: a => badge(a.severity === "critical" ? "deny" : "instruct", a.severity) },
    { label: "channels", get: a => (a.channels || []).join(", ") || "—" }, { label: "", html: a => `<button data-test="${a.id}">Test</button> <button data-del="${a.id}">Delete</button>` }], al)}`;
  const body = () => ({ text: v.querySelector("#t").value, severity: v.querySelector("#sev").value, channels: v.querySelector("#ch").value.split(",").map(s => s.trim()).filter(Boolean) });
  v.querySelector("#prev").onclick = async () => { const r = await post("/alerts/preview", body()); v.querySelector("#pv").textContent = `${JSON.stringify(r.spec)} → would have fired ${r.would_fire} times in the last 24h`; };
  v.querySelector("#add").onclick = async () => { await post("/alerts", body()); route(); };
  v.querySelectorAll("[data-test]").forEach(b => b.onclick = async () => { const r = await post(`/alerts/${b.dataset.test}/test`); alert(`sent: ${r.sent.join(", ") || "none"}\nerrors: ${JSON.stringify(r.errors)}`); });
  v.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => { await api("/alerts/" + b.dataset.del, { method: "DELETE" }); route(); });
};

DG.pages.settings = async (v) => {
  const s = await api("/settings");
  v.innerHTML = `<h1>Settings</h1><div class="note small">Secrets (API keys, bot tokens) are never shown here. Set them with the CLI: <span class="mono">dinoguard reviewer setup --key-stdin</span>, <span class="mono">dinoguard approve setup --telegram</span>.</div>
  <div class="cols">
  <div class="card"><b>Reviewer (LLM checks)</b><div class="row"><input id="rurl" placeholder="OpenAI-compatible URL" value="${esc(s.reviewer.url || "")}"><input id="rmodel" value="${esc(s.reviewer.model || "")}"><select id="rmode"><option ${s.reviewer.mode === "observe" ? "selected" : ""}>observe</option><option ${s.reviewer.mode === "enforce" ? "selected" : ""}>enforce</option></select></div></div>
  <div class="card"><b>Approvals</b><div class="row"><input id="ach" placeholder="channel name" value="${esc(s.approvals.channel || "")}"><input id="ato" type="number" value="${s.approvals.timeout_s || 120}"> s</div></div>
  <div class="card"><b>Budget</b><div class="row">$ <input id="bud" type="number" step="0.5" value="${s.budget_usd ?? ""}" placeholder="per session"></div><p class="muted small">Enforced by the token-budget policy (enable it on Policies).</p></div>
  <div class="card"><b>Runner</b><div class="row">high burn ≥ <input id="burn" type="number" value="${s.runner.burn_high_per_min}"> tokens/min</div></div>
  <div class="card"><b>Prices</b><p class="small">Edit <span class="mono">${esc(s.prices_file)}</span> ($ per 1M tokens per model).</p></div>
  <div class="card"><b>Alert channels</b><pre class="box">${esc(JSON.stringify(s.channels, null, 2))}</pre><p class="muted small">Add: <span class="mono">dinoguard channel ops slack webhook_url=…</span></p></div>
  </div><div class="row"><button id="save" class="primary">Save</button><span id="ok" class="muted"></span></div>`;
  v.querySelector("#save").onclick = async () => {
    const num = id => { const x = v.querySelector(id).value; return x === "" ? null : +x; };
    await post("/settings", { reviewer: { url: v.querySelector("#rurl").value || null, model: v.querySelector("#rmodel").value, mode: v.querySelector("#rmode").value },
      approvals: { channel: v.querySelector("#ach").value || null, timeout_s: num("#ato") || 120 }, runner: { burn_high_per_min: num("#burn") || 20000 }, budget_usd: num("#bud") });
    v.querySelector("#ok").textContent = "saved"; };
};
