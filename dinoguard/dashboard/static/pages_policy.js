/* Policies, Metrics, Queries */
DG.pages.policies = async (v) => {
  const d = await api("/policies");
  const cats = [...new Set(d.policies.map(p => p.category))].sort();
  v.innerHTML = `<h1>Policies</h1>
  <div class="note small">Enforced policies block or steer the agent. Observe mode records what a policy <i>would</i> do without enforcing it; new custom policies start in observe. <b>guard-self-protect</b> is always on.</div>
  <div class="filters"><select id="cat"><option value="">all categories</option>${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select>
  <select id="state"><option value="">any state</option><option value="on">enabled</option><option value="off">disabled</option></select>
  <input id="search" placeholder="search"></div>
  <div id="list"></div><div id="detail"></div>
  ${d.errors.length ? `<h2>Load errors</h2>${table([{ label: "file", key: "file" }, { label: "error", key: "error" }], d.errors)}` : ""}
  <h2>Draft a new policy</h2><div class="card"><div class="row"><input id="nl" style="flex:1" placeholder='never touch the payments folder'><button id="draft">Draft</button></div>
  <p class="muted small">Uses the configured LLM if any, else a template. Then: <span class="mono">dinoguard policy test / backtest / publish / deploy --mode observe</span>.</p><pre id="draftout" class="box hidden"></pre></div>`;
  const render = () => {
    const c = v.querySelector("#cat").value, st = v.querySelector("#state").value, q = v.querySelector("#search").value.toLowerCase();
    const rows = d.policies.filter(p => (!c || p.category === c) && (!st || (st === "on") === p.enabled) && (!q || (p.name + p.description).toLowerCase().includes(q)));
    const el = v.querySelector("#list");
    el.innerHTML = table([
      { label: "policy", html: p => `<b>${esc(p.name)}</b><div class="muted small">${esc(p.description)}</div>` },
      { label: "category", key: "category" }, { label: "authority", key: "authority" }, { label: "source", key: "source" },
      { label: "activity", html: p => Object.entries(p.activity).map(([k, n]) => badge(k.split("_")[0], `${k.replace("_", " ")} ${n}`)).join(" ") || '<span class="muted">—</span>' },
      { label: "enforcement", html: p => p.always_on ? badge("enforce", "always on") :
        `<select data-p="${esc(p.name)}" class="mode"><option value="off" ${!p.enabled ? "selected" : ""}>off</option>
         <option value="observe" ${p.enabled && p.mode === "observe" ? "selected" : ""}>observe</option>
         <option value="enforce" ${p.enabled && p.mode === "enforce" ? "selected" : ""}>enforce</option></select>` }], rows, { onRow: true });
    el.querySelectorAll("select.mode").forEach(s => { s.addEventListener("click", e => e.stopPropagation());
      s.addEventListener("change", async () => { const body = s.value === "off" ? { enabled: false } : { enabled: true, mode: s.value };
        await post("/policies/" + encodeURIComponent(s.dataset.p), body); const p = d.policies.find(x => x.name === s.dataset.p); p.enabled = s.value !== "off"; if (p.enabled) p.mode = s.value; }); });
    bindRows(el, rows, showDetail);
  };
  async function showDetail(p) {
    const act = await api("/policies/" + encodeURIComponent(p.name) + "/activity");
    v.querySelector("#detail").innerHTML = `<h2>${esc(p.name)} — activity (why it matched)</h2>
      <pre class="box">${esc(JSON.stringify({ events: p.events, params: p.params, reviewed_by: p.reviewed_by, file: p.file, version: p.version }, null, 2))}</pre>
      ${table([{ label: "time", get: r => fmtTs(r.ts) }, { label: "decision", html: r => badge(r.decision) }, { label: "mode", key: "mode" },
        { label: "action", get: r => { try { return summarize(JSON.parse(r.input_json)).slice(0, 90); } catch { return ""; } } },
        { label: "reason", get: r => (r.reason || "").slice(0, 140) }, { label: "session", html: r => `<a href="#/trace/${encodeURIComponent(r.session_id)}">${esc(r.session_id.slice(0, 10))}</a>` }], act, { empty: "This policy has not fired yet." })}`;
    v.querySelector("#detail").scrollIntoView({ behavior: "smooth" });
  }
  ["cat", "state", "search"].forEach(id => v.querySelector("#" + id).addEventListener("input", render));
  v.querySelector("#draft").onclick = async () => { const out = v.querySelector("#draftout");
    try { const r = await post("/policies-draft", { text: v.querySelector("#nl").value }); out.textContent = `# ${r.name} (${r.method}) → ${r.path}\n\n${r.code}`; }
    catch (e) { out.textContent = e.message; } out.classList.remove("hidden"); };
  render();
};

DG.pages.metrics = async (v) => {
  const m = await api("/metrics");
  const latRows = [["hook decisions", m.hook_latency_ms], ...Object.entries(m.tool_latency_ms).map(([k, x]) => ["tool: " + k, x]),
    ...Object.entries(m.model_latency_ms).map(([k, x]) => ["model: " + k, x])].map(([name, x]) => ({ name, ...x }));
  v.innerHTML = `<h1>Metrics <span class="muted small">last ${m.hours / 24} days</span></h1>
  <h2>Latency (ms)</h2>${table([{ label: "what", key: "name" }, { label: "samples", key: "n" }, { label: "p50", get: r => r.p50 ?? "—" }, { label: "p95", get: r => r.p95 ?? "—" }, { label: "p99", get: r => r.p99 ?? "—" }], latRows)}
  <div class="cols"><div id="cost"></div><div id="ctx"></div></div>
  <div id="analytics"></div>`;
  metricsAnalytics(v.querySelector("#analytics"));
  hBars(v.querySelector("#cost"), m.cost_per_model.map(r => ({ label: r.model || "?", value: r.cost || 0, fmt: money })), { title: "Cost per model" });
  hBars(v.querySelector("#ctx"), m.context_usage.slice(0, 10).map(r => ({ label: r.session_id.slice(0, 12), value: r.ctx || 0 })), { title: "Peak context tokens per session", color: "var(--series-3)" });
};

DG.pages.queries = async (v) => {
  v.innerHTML = `<h1>Queries</h1><div class="note small">Read-only SQL over the local store (SELECT only, 5 s timeout). Tables: sessions, events, decisions, model_calls, policies, findings, insights, alerts, eval_results, approvals.</div>
  <textarea id="sql" class="mono">SELECT tool_name, decision, COUNT(*) n FROM events WHERE event='PreToolUse' GROUP BY 1,2 ORDER BY n DESC</textarea>
  <div class="row"><button id="run" class="primary">Run</button><input id="tname" placeholder="tile name"><button id="save">Save as tile</button></div>
  <div id="out"></div><h2>Dashboard tiles</h2><div id="tiles" class="cols"></div>`;
  const run = async (sql, el) => { try { const r = await post("/query", { sql });
      el.innerHTML = table(r.columns.map((c, i) => ({ label: c, get: row => row[i] })), r.rows.slice(0, 200), { empty: "No rows." }); }
    catch (e) { el.innerHTML = `<pre class="box">${esc(e.message)}</pre>`; } };
  v.querySelector("#run").onclick = () => run(v.querySelector("#sql").value, v.querySelector("#out"));
  const loadTiles = async () => { const qs = await api("/queries"); const el = v.querySelector("#tiles");
    el.innerHTML = qs.map(q => `<div class="card"><div class="row"><b>${esc(q.name)}</b><button class="ghost" data-del="${q.id}">✕</button></div><div data-q="${q.id}"></div></div>`).join("") || '<p class="muted">Save a query to pin it here.</p>';
    qs.forEach(q => run(q.sql, el.querySelector(`[data-q="${q.id}"]`)));
    el.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => { await api("/queries/" + b.dataset.del, { method: "DELETE" }); loadTiles(); }); };
  v.querySelector("#save").onclick = async () => { await post("/queries", { name: v.querySelector("#tname").value || "tile", sql: v.querySelector("#sql").value }); loadTiles(); };
  loadTiles();
};
