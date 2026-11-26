/* Home, Sessions, Trace, Live */
DG.pages.home = async (v) => {
  const d = await api("/home");
  v.innerHTML = `<h1>Today</h1>
  <div class="grid">${tile("Actions (24h)", fmtNum(d.actions))}${tile("Allowed", fmtNum(d.allowed), badge("allow"))}
  ${tile("Instructed", fmtNum(d.instructed), badge("instruct"))}${tile("Denied", fmtNum(d.denied), badge("deny"))}
  ${tile("Cost (24h)", money(d.cost_usd))}${tile("Tokens (24h)", fmtNum(d.tokens))}
  ${tile("Sessions (24h)", fmtNum(d.sessions))}${tile("Open insights", `<a href="#/insights">${d.open_insights}</a>`)}</div>
  <h2>Ask anything about your agents</h2>
  <form id="home-ask" class="row"><input id="home-q" style="flex:1" placeholder="which policies blocked the most this week?"><button class="primary">Ask</button></form>
  <div id="days"></div>
  <h2>Most active policies (7 days)</h2>
  ${table([{ label: "policy", key: "policy" }, { label: "decision", html: r => badge(r.decision) }, { label: "count", key: "n" }], d.top_policies, { empty: "No policy has fired yet." })}`;
  stackedBars(v.querySelector("#days"), d.days.map(x => ({ label: x.day.slice(5), values: x })),
    [{ key: "allow", label: "allow", color: STATUS.allow }, { key: "instruct", label: "instruct", color: STATUS.instruct },
     { key: "deny", label: "deny", color: STATUS.deny }], { title: "Tool calls per day by decision (14 days)" });
  v.querySelector("#home-ask").addEventListener("submit", e => { e.preventDefault(); DG.openDock(v.querySelector("#home-q").value); });
};

DG.pages.sessions = async (v) => {
  const p = new URLSearchParams(location.hash.split("?")[1] || "");
  v.innerHTML = `<h1>Sessions</h1><form class="filters" id="f">
    <select name="harness"><option value="">any harness</option><option>claude</option><option>cursor</option><option>codex</option><option>sdk</option><option>otel</option></select>
    <input name="project" placeholder="project path contains"><select name="status"><option value="">any status</option><option>active</option><option>ended</option></select>
    <input name="since" type="date"><input name="session" placeholder="session id" size="10"><input name="model" placeholder="model" size="10">
    <input name="environment" placeholder="environment" size="10"><select name="event"><option value="">any event</option><option>PreToolUse</option><option>PostToolUseFailure</option><option>Stop</option><option>SubagentStart</option><option>PreCompact</option></select>
    <input name="min_cost" type="number" step="0.01" placeholder="min $" style="width:6em"><input name="min_tokens" type="number" placeholder="min tokens" style="width:8em">
    <label class="small"><input name="has_deny" type="checkbox" value="true"> has deny</label><button>Filter</button></form>
    <form class="filters" id="cmp"><input name="a" placeholder="session A" size="12"><input name="b" placeholder="session B" size="12"><button>Compare</button>
    <a href="#/compare" class="small">compare groups (model, harness, environment, outcome)</a></form><div id="list"></div>`;
  v.querySelector("#cmp").addEventListener("submit", e => { e.preventDefault(); const c = new FormData(e.target);
    location.hash = "#/compare/" + encodeURIComponent(c.get("a")) + "/" + encodeURIComponent(c.get("b")); });
  const f = v.querySelector("#f");
  for (const [k, val] of p) if (f.elements[k]) f.elements[k].value = val;
  async function load() {
    const q = new URLSearchParams([...new FormData(f)].filter(([, x]) => x));
    const rows = await api("/sessions?" + q);
    const el = v.querySelector("#list");
    el.innerHTML = table([
      { label: "last activity", get: r => fmtTs(r.last_ts) }, { label: "harness", key: "harness" },
      { label: "project", get: r => (r.cwd || "").split(/[\\/]/).slice(-2).join("/") },
      { label: "first prompt", get: r => (r.first_prompt || "").slice(0, 80) },
      { label: "tools", key: "tools" }, { label: "denied", html: r => r.denies ? badge("deny", r.denies) : "0" },
      { label: "cost", get: r => money(r.cost) }, { label: "status", key: "status" }], rows, { onRow: true, empty: "No sessions recorded yet. Run `dinoguard install` and use your agent." });
    bindRows(el, rows, r => location.hash = "#/trace/" + encodeURIComponent(r.id));
  }
  f.addEventListener("submit", e => { e.preventDefault(); load(); });
  load();
};

function traceItem(it, child) {
  if (it.type === "model") return `<div class="trace-item model ${child ? "child" : ""}" data-id="${it.id}"><span class="muted small">${fmtTs(it.ts)}</span> <b>model</b> ${esc(it.model)} · in ${fmtNum(it.tokens_in)} · out ${fmtNum(it.tokens_out)} · cache ${fmtNum(it.cache_read)} · ${money(it.cost_usd)}</div>`;
  if (it.type === "human") return `<div class="trace-item human" data-id="${it.id}"><span class="muted small">${fmtTs(it.ts)}</span> <b>human approval</b> ${esc(it.summary)} → ${esc(it.answer)}</div>`;
  const decs = it.decisions.map(d => `<div>${badge(d.decision)} <b>${esc(d.policy)}</b> <span class="muted small">${esc(d.mode)}${d.enforced ? "" : " (not enforced)"}${d.error ? " · error: " + esc(d.error) : ""}</span>
     ${d.agent_message ? `<div class="agent-msg mono">agent received: ${esc(d.agent_message)}</div>` : `<div class="muted small">${esc(d.reason || "")}</div>`}
     ${d.reviewer_verdict ? `<div class="muted small">reviewer: ${esc(JSON.stringify(d.reviewer_verdict))}</div>` : ""}</div>`).join("");
  const kids = it.children.map(c => traceItem(c, true)).join("");
  return `<div class="trace-item ${it.decision || ""} ${child ? "child" : ""}" data-id="${it.id}">
    <span class="muted small">${fmtTs(it.ts)}</span> <b>${esc(it.event)}</b> ${it.tool ? esc(it.tool) : ""} ${it.decision && it.event === "PreToolUse" ? badge(it.decision) : ""}
    ${it.agent_id ? `<span class="badge">sub-agent ${esc(it.agent_id.slice(0, 8))}</span>` : ""}
    <div class="mono small">${esc(summarize(it.input)).slice(0, 300)}</div>
    ${it.error ? `<div class="small" style="color:var(--critical)">error: ${esc(it.error)}</div>` : ""}
    ${it.checkpoint ? `<div class="muted small">git checkpoint before this (${esc(it.checkpoint.reason)}): <span class="mono">dinoguard undo ${esc(it.id.slice(0, 16))}</span></div>` : ""}
    ${it.output ? `<details><summary class="muted small">output</summary><pre class="box">${esc(it.output)}</pre></details>` : ""}
    ${decs}${kids}</div>`;
}

function graphSvg(g) {
  const W = 760, nodeW = 170, nodeH = 54, gap = 26;
  const subs = g.nodes.filter(n => n.id !== "main");
  const H = Math.max(110, 40 + (subs.length ? 90 : 0) + nodeH);
  const pos = { main: { x: W / 2 - nodeW / 2, y: 16 } };
  subs.forEach((n, i) => { const total = subs.length * (nodeW + gap) - gap; pos[n.id] = { x: Math.max(0, W / 2 - total / 2) + i * (nodeW + gap), y: 120 }; });
  const h2 = Math.max(H, subs.length ? 190 : 90);
  let s = `<svg viewBox="0 0 ${Math.max(W, subs.length * (nodeW + gap))} ${h2}" role="img" aria-label="execution graph">`;
  g.edges.forEach(e => { const a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
    s += `<path d="M${a.x + nodeW / 2},${a.y + nodeH} C${a.x + nodeW / 2},${a.y + nodeH + 30} ${b.x + nodeW / 2},${b.y - 30} ${b.x + nodeW / 2},${b.y}" fill="none" stroke="var(--text-muted)" stroke-width="1.5"/>
    <text x="${(a.x + b.x) / 2 + nodeW / 2}" y="${(a.y + nodeH + b.y) / 2}" font-size="11" fill="var(--text-muted)" text-anchor="middle">${esc(String(e.via).slice(0, 28))}</text>`; });
  g.nodes.forEach(n => { const p = pos[n.id];
    s += `<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${nodeH}" rx="8" fill="var(--surface-2)" stroke="${n.denies ? "var(--critical)" : "var(--line)"}" stroke-width="${n.denies ? 2 : 1}"/>
    <text x="${p.x + 10}" y="${p.y + 21}" font-size="12.5" fill="var(--text-primary)" font-weight="600">${esc(n.label)}</text>
    <text x="${p.x + 10}" y="${p.y + 40}" font-size="11.5" fill="var(--text-secondary)">${n.tools} tool calls · ${n.denies} denied</text>`; });
  return s + "</svg>";
}

DG.pages.trace = async (v, [sid]) => {
  const t = await api("/sessions/" + encodeURIComponent(sid) + "/trace");
  const s = t.session;
  const ex = "/api/sessions/" + encodeURIComponent(sid) + "/export";
  v.innerHTML = `<h1>Trace <span class="muted mono small">${esc(sid)}</span></h1>
  <div class="row small">Export (redacted): <a href="${ex}" download="dinoguard-${esc(sid.slice(0, 12))}.json">JSON</a> · <a href="${ex}?format=jsonl">JSONL</a>
    · environment: <b>${esc(s.environment || "—")}</b> · model: <b>${esc(s.model || "—")}</b></div>
  <div class="grid">${tile("Harness", esc(s.harness))}${tile("Events", t.counts.events)}${tile("Model calls", t.counts.model_calls)}
  ${tile("Cost", money(s.cost_usd))}${tile("Status", esc(s.status))}</div>
  <p class="muted">Project: <span class="mono">${esc(s.cwd || "")}</span> · First prompt: ${esc(s.first_prompt || "—")}</p>
  <h2>Execution graph</h2><div class="chart">${graphSvg(t.graph)}</div>
  <h2>Blocked or steered (${t.blocked.length})</h2>
  ${table([{ label: "time", get: r => fmtTs(r.ts) }, { label: "decision", html: r => badge(r.decision) }, { label: "policy", key: "policy" },
    { label: "action", get: r => summarize(r.input).slice(0, 80) }, { label: "exact message the agent received", html: r => `<span class="mono small">${esc(r.message)}</span>` }], t.blocked, { empty: "Nothing was blocked in this session." })}
  <div class="note small">Post-tool decisions happen after the tool ran: they can tell the agent, but cannot undo side effects.</div>
  <h2>Timeline</h2><div class="row"><button id="replay">▶ Replay</button><button id="step">Step</button><span id="pos" class="muted small"></span></div>
  <div id="tl">${t.items.map(i => traceItem(i, false)).join("")}</div>`;
  const nodes = [...v.querySelectorAll("#tl > .trace-item")]; let idx = -1, timer = null;
  const show = i => { nodes.forEach((n, j) => { n.classList.toggle("hl", j === i); n.style.opacity = j <= i ? 1 : .35; });
    if (nodes[i]) nodes[i].scrollIntoView({ block: "nearest" }); v.querySelector("#pos").textContent = `${i + 1}/${nodes.length}`; };
  v.querySelector("#step").onclick = () => { idx = (idx + 1) % nodes.length; show(idx); };
  v.querySelector("#replay").onclick = () => { if (timer) { clearInterval(timer); timer = null; return; } idx = -1;
    timer = setInterval(() => { idx++; if (idx >= nodes.length) { clearInterval(timer); timer = null; nodes.forEach(n => n.style.opacity = 1); return; } show(idx); }, 600);
    DG.timers.push(timer); };
};

DG.pages.live = async (v) => {
  v.innerHTML = `<h1>Live</h1><iframe src="/runner?embed=1" title="dino runner" style="width:100%;height:220px;border:1px solid var(--line);border-radius:10px;background:#000"></iframe>
  <h2>Event stream</h2><div class="live-log" id="log"></div>`;
  let offset = -1; const log = v.querySelector("#log");
  async function poll() {
    const r = await api("/stream?after=" + offset); offset = r.offset;
    r.records.forEach(x => {
      const kind = x.kind === "event" ? (x.decision || "allow") : x.kind;
      log.prepend(h(`<div class="trace-item ${x.decision || ""}"><span class="muted small">${fmtTs(x.ts)}</span> ${x.kind === "event" ? badge(x.decision) : `<span class="badge">${esc(kind)}</span>`}
        <b>${esc(x.event || "")}</b> ${esc(x.tool || "")} <span class="mono small">${esc(x.summary || "")}</span>
        ${x.reason ? `<div class="muted small">[${esc(x.policy)}] ${esc(x.reason)}</div>` : ""}
        ${(x.observed || []).length ? `<div class="muted small">observe: ${x.observed.map(o => esc(o.policy) + " would " + esc(o.decision)).join(", ")}</div>` : ""}</div>`));
    });
    while (log.children.length > 300) log.lastChild.remove();
  }
  await poll(); DG.timers.push(setInterval(poll, 1000));
};
