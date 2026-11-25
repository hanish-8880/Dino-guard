# PROGRESS

Built in one pass on 2026-11-25, phase by phase per CLAUDE.md §14. `pytest -q` was green at the end of every phase.
Current: **398 tests passing**. Every file is under 400 lines (largest is 255).

## Phase 1 — Hook + log (Claude Code) ✅
- `dinoguard install` merges hooks into Claude Code settings (user/project/local) and backs up the original.
  `uninstall` restores the exact bytes (tested), and re-install is idempotent.
- Hook → adapter → canonical event → SQLite (`events`, `sessions`, `decisions`) + `stream.jsonl`. Pre/Post events
  are linked by `tool_use_id`. Secrets are redacted before storage (tested).
- The adapter was verified against the current hooks docs: `docs/adapters/claude-code.md`.

## Phase 2 — Policy engine + core rules ✅
- Engine features:
  - load order, with built-ins first and self-protect first of all
  - first deny wins; instructs are merged
  - 10 s per-policy deadline (error or timeout → allow, except self-protect → deny)
  - observe/enforce modes; hard/reviewable authority (malformed → hard)
  - unique names, module-load deadline, narrow pack-unavailable deny
- 50 built-ins in 12 modules, with table-driven tests (≥2 should-fire + ≥2 should-allow each). The parser unwraps
  `bash -lc "..."`, `sh -c`, `$(...)` and `eval` so nested commands are checked too.
- Done-when: `rm -rf src`, `cat .env` and `git push --force` are denied; `DROP TABLE` is instructed; `ls` is
  allowed; disabling the guard is denied.

## Phase 3 — Daemon + fail-closed ✅
- `dinoguardd` uses AF_UNIX on POSIX. **On Windows (no AF_UNIX in CPython) it uses 127.0.0.1 TCP + a port file +
  a random auth token.**
- Daemon down, timeout, version skew or bad token → every guarded action is denied with a `dinoguard doctor` hint.
  Verified end to end on Windows with a real detached daemon.
- `doctor`, `status`, and `daemon start|stop|status|restart` are in place. The daemon also runs alert checks and
  scheduled audits every 30 s.

## Phase 4 — Dino runner + tokens ✅
- An original braille "plated guard-dino" with 9 states: walk, sprint, instruct, deny, shield, review, wait,
  ghost, guard-down.
- The terminal strip (`rich.live`) measured **2.1% CPU at 20 fps**, 0.25 ms per frame. It renders at 10–200 columns
  and survives stream rotation.
- Tokens are read incrementally from the transcript (byte offset), priced from the user-editable `prices.yaml`, with
  burn rate, context % and cost. Token-coaching policies, the budget stop and the loop hard-stop are in place.
- `dinoguard demo` drives the real engine through every state in a throwaway home. `docs/runner.gif` is generated
  from the real scene code.

## Phase 5 — Dashboard v1 ✅ (plus pages from later phases)
- FastAPI + plain HTML/JS, no build step. Pages: Home, Sessions, Trace (nested timeline, sub-agent execution graph,
  "exact message the agent received", replay), Live (stream + web runner), Policies (activity + enforcement +
  drafting), Audit, Findings & insights, Evaluations, Alerts, Metrics (p50/p95/p99, cost per model, context, error
  rates), Queries (SELECT-only, 5 s timeout, saved tiles), Settings, and Ctrl+J assistant dock.
- Every write endpoint needs a per-install token (blocks drive-by CSRF), and the server is local-only.
  Self-protect also denies the agent POSTing to `localhost:8030`.
- Pages were screenshot-checked in headless Edge. The web runner canvas was verified by pixel probe, because headless
  screenshots don't capture canvases.

## Phase 6 — Approvals + alerts ✅
- `phone-approval` holds the action. The answer can come from Telegram (polled), the WhatsApp webhook (Twilio, or the
  Meta Cloud API for sending), the dashboard, or `dinoguard approve answer <code> yes`. NO or timeout → deny.
- Alerts can trigger on metric thresholds, errors, eval score, eval combos or custom SQL. "Tell me when …" is parsed
  rule-based, with an LLM fallback. They support check interval, consecutive checks, edge-triggered firing (which
  opens an insight) and a 24 h "would have fired N times" preview.
- Channels: WhatsApp, Telegram, Slack, email (SMTP), and webhook with HMAC-SHA256. Secrets live in `secrets.json`,
  never in `config.json`.

## Phase 7 — Reviewer + failure-mode policies ✅
- `check()` declarations support 1–6 probes, exempt, mode deny/instruct, and the 6 preconditions.
- A reviewable deny is cleared only if **every** `reviewed_by` check answers allow/warn, and only in enforce mode.
  Hard policies always win. Error, timeout or a missing check → the policy stands.
- Verdicts are cached per session in SQLite. Standalone checks can warn/deny on their own. The provider is any
  OpenAI-compatible endpoint.
- tool-not-found, goal-drift, intent-complete and destructive-command already existed from Phase 2.

## Phase 8 — Audit, authoring, backtest, versions ✅
- The audit replays `~/.claude/projects/**/*.jsonl` through all built-ins (observe-only) into findings and insights,
  with schedule (1–90 days) and email. On this machine it scanned **4.5k real tool calls in ~29 s**, after a 4.4×
  speed-up from caching shell parsing.
- `policy new` → LLM draft, or a template (path/command/regex) without an LLM. Then `test` (2 block + 2 allow
  examples) → `backtest` (would-block / would-interrupt working calls) → `publish` (immutable) → `deploy --mode
  observe|enforce` → `rollback`. Findings have a "Draft a policy" button.

## Phase 9 — Evals + sentiment + assistant + MCP ✅
- Code evals use a restricted AST evaluator (no attributes, dunders or imports; whitelisted functions; constant caps;
  2 s limit). Judge and classifier use the LLM; worker evals use HTTP endpoints.
- Lifecycle: test (not stored) → deploy (versioned) → scores new sessions on SessionEnd → backfill.
- The built-in **sentiment** eval (lexicon frustration score) is deployed by default.
- `dinoguard ask` and the dashboard assistant turn questions into read-only SQL (LLM, or templates without one),
  show the SQL and cite session ids. `dinoguard mcp` serves `list_sessions`, `get_trace`, `query`,
  `list_findings` and `explain_decision` over stdio.

## Phase 10 — Packs + more adapters + SDK ✅ (some adapters unverified, see below)
- Packs: manifest validation; SHA256SUMS check plus a pinned digest re-verified before every load (tamper or retag →
  refuse). Pack policy names are prefixed `<pack>/` and can't reuse built-in names. An enforced pack that's missing
  policies gives a narrow deny (prompt-submit → instruct; pause, observe, disabled and timeout are exempt).
  `show` reads the manifest only, and `dinoguard publish` builds a pack.
- Adapters + installers: Cursor, Gemini CLI and Copilot are **verified against current docs**. **Codex and OpenCode
  are partly unverified** (payload/block format not documented on the pages read). **Goose is a stub** (no hook
  mechanism exists). See `docs/adapters/*.md`.
- SDK: `dinoguard.check(...)`, plus `@guard` / `guard_tools` / LangGraph helper. OTLP/JSON ingest is at
  `/api/v1/traces`.

## Phase 11 — Showcase polish ✅ (partial)
- A first-run wizard (skipped with `DG_NO_FIRST_RUN=1` or without a TTY), `demo/run_demo.py`, `demo/make_gif.py`,
  and a README with the GIF and the required attribution line.
- **Not done:** publishing to PyPI. `pipx install dinoguard` only works once you publish; until then use
  `pip install -e .`.

## Decisions / deviations (please review)
- Enabled built-ins run in **enforce** by default (Phase 2's done-when needs it). New custom, pack and deployed
  policies default to **observe**, as CLAUDE.md says.
- `allow` never emits `permissionDecision: "allow"`, because that would skip the user's own permission prompts.
- The Claude Code `PowerShell` tool is mapped to canonical `Bash`, so the shell rules cover Windows too.
- While the daemon is down, Stop/SubagentStop hooks are **not** blocked; otherwise the agent could be trapped in
  an endless turn. Tool calls are still denied.
- Audit skips `tool-not-found` and `use-repo-package-manager`, which depend on today's disk rather than history.
- Audit calls policies directly, without the per-policy deadline thread, for speed. A hung policy would hang an
  audit, but never a live hook.
- Price table values are estimates; edit `~/.dinoguard/prices.yaml`.

## Not done / open items
- **Never installed into your real `~/.claude/settings.json`.** That would have hooked the live build session.
  All install/uninstall testing used temporary settings files. To try it: `dinoguard install`, then
  `dinoguard doctor`.
- Phase 4's done-when ("a live Claude Code session animates correctly") was checked with simulated hook payloads
  and the demo, not a live session.
- WhatsApp/Telegram/Slack/email delivery and the LLM reviewer/judge/drafting are implemented, but untested against
  real services (no credentials). They are covered with fakes.
- Codex: confirm the payload/output at https://developers.openai.com/codex/hooks. OpenCode: confirm a thrown
  error blocks `tool.execute.before` (or switch to the `permission.hook("evaluate")` API).
- Pack install from GitHub was only tested from a local folder (no network test).
