// DinoGuard client for Node 18+ / Deno / Bun (no dependencies, no build step). CLAUDE.md §17.9.
// Talks to the local dashboard: POST http://127.0.0.1:8030/api/v1/events with the per-install token.
//
//   import { DinoGuard } from "./dinoguard.mjs";
//   const dg = new DinoGuard({ agentId: "support-bot", environment: "staging" });
//   await dg.start();
//   const d = await dg.toolCall("Bash", { command: "ls" });   // throws DinoGuardDenied on deny
//   await dg.toolResult("Bash", { command: "ls" }, { output: "a.txt", durationMs: 12 });
//   await dg.end();

export class DinoGuardDenied extends Error {
  constructor(decision) { super(decision.reason || "denied by DinoGuard"); this.name = "DinoGuardDenied"; this.decision = decision; }
}

async function readToken() {
  if (typeof process !== "undefined" && process.env && process.env.DINOGUARD_TOKEN) return process.env.DINOGUARD_TOKEN;
  try {
    const { readFile } = await import("node:fs/promises");
    const { homedir } = await import("node:os");
    const { join } = await import("node:path");
    const home = (process.env && process.env.DINOGUARD_HOME) || join(homedir(), ".dinoguard");
    return (await readFile(join(home, "dashboard.token"), "utf8")).trim();
  } catch { return ""; }
}

export class DinoGuard {
  constructor(opts = {}) {
    this.url = (opts.url || "http://127.0.0.1:8030").replace(/\/$/, "");
    this.sessionId = opts.sessionId || "sdk-" + Math.random().toString(16).slice(2, 18);
    this.agentId = opts.agentId || null;
    this.environment = opts.environment || null;
    this.harness = opts.harness || "sdk-ts";
    this.cwd = opts.cwd || (typeof process !== "undefined" ? process.cwd() : "");
    this.raiseOnDeny = opts.raiseOnDeny !== false;
    this.failClosed = opts.failClosed !== false;   // DinoGuard unreachable -> deny, like the hooks
    this.token = opts.token || null;
    this.calls = 0;
  }

  async send(events) {
    const token = this.token || (this.token = await readToken());
    try {
      const r = await fetch(this.url + "/api/v1/events", {
        method: "POST", headers: { "Content-Type": "application/json", "X-DinoGuard-Token": token },
        body: JSON.stringify({ events }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return (await r.json()).decisions;
    } catch (e) {
      if (!this.failClosed) return events.map(() => ({ decision: "allow", reason: null, policy: null }));
      return events.map(() => ({ decision: "deny", policy: "dinoguard/unavailable",
        reason: `[DinoGuard] could not reach DinoGuard (${e.message}). Run \`dinoguard dashboard\` and \`dinoguard doctor\`.` }));
    }
  }

  async event(event, fields = {}) {
    const ev = { event, session_id: this.sessionId, agent_id: this.agentId, environment: this.environment,
      harness: this.harness, cwd: this.cwd, ...fields };
    return (await this.send([ev]))[0];
  }

  gate(d) { if (d.decision === "deny" && this.raiseOnDeny) throw new DinoGuardDenied(d); return d; }

  start(meta = {}) { return this.event("SessionStart", { extra: meta }); }
  end(status = "completed", meta = {}) { return this.event("SessionEnd", { extra: { status, ...meta } }); }
  async prompt(text) { return this.gate(await this.event("UserPromptSubmit", { prompt: text })); }
  stop(final = "") { return this.event("Stop", { extra: { final } }); }
  modelRequest(model, meta = {}) { return this.event("ModelRequest", { extra: { model, ...meta } }); }
  modelResponse(model, u = {}) {
    return this.event("ModelResponse", { extra: { model, tokens_in: u.tokensIn || 0, tokens_out: u.tokensOut || 0,
      cache_read: u.cacheRead || 0, cache_write: u.cacheWrite || 0, cost_usd: u.costUsd ?? null, latency_ms: u.latencyMs ?? null } });
  }
  async toolCall(tool, input = {}, toolUseId = null) {
    this.calls += 1;
    return this.gate(await this.event("PreToolUse", { tool, input, tool_use_id: toolUseId || `${this.sessionId}:${this.calls}:${tool}` }));
  }
  toolResult(tool, input = {}, { output = null, error = null, durationMs = null, toolUseId = null } = {}) {
    return this.event(error ? "PostToolUseFailure" : "PostToolUse", { tool, input, error, duration_ms: durationMs,
      output: output == null ? null : (typeof output === "string" ? output : JSON.stringify(output)).slice(0, 16000),
      tool_use_id: toolUseId || `${this.sessionId}:${this.calls}:${tool}` });
  }
  async runTool(tool, input, fn) {
    await this.toolCall(tool, input);
    const t0 = Date.now();
    try { const res = await fn(); await this.toolResult(tool, input, { output: res, durationMs: Date.now() - t0 }); return res; }
    catch (e) { if (!(e instanceof DinoGuardDenied)) await this.toolResult(tool, input, { error: String(e), durationMs: Date.now() - t0 }); throw e; }
  }
  hookStart(name, meta = {}) { return this.event("HookStart", { extra: { hook: name, ...meta } }); }
  hookEnd(name, meta = {}) { return this.event("HookEnd", { extra: { hook: name, ...meta } }); }
  humanWait(reason = "") { return this.event("HumanWait", { extra: { reason } }); }
  humanInput(text = "") { return this.event("HumanInput", { prompt: text || null }); }
  interruption(reason = "") { return this.event("Interruption", { extra: { reason } }); }
  error(err) { return this.event("AgentError", { error: String(err && err.stack || err).slice(0, 1000) }); }
  pause(reason = "") { return this.event("AgentPause", { extra: { reason } }); }
  resume() { return this.event("AgentResume", {}); }
}
