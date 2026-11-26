# PROGRESS

Built in one pass on 2026-11-25, phase by phase per CLAUDE.md §14. `pytest -q` was green at the end of every phase.
Current: **510 tests passing** (after Phases 12–13 on 2026-11-26). Every file is under 400 lines (largest is 342).

## Phase 13 — SDK lifecycle, more harnesses, MCP writes, undo ✅ (2026-11-26, CLAUDE.md §17.9–17.12)
Tests: `tests/test_sdk_tracer.py`, `tests/test_more_harnesses.py`, `tests/test_mcp_undo.py`, plus new cases in
`tests/test_daemon.py`.

| § | Item | Where |
|---|---|---|
| 17.9 | `dinoguard.trace_session(...)`: prompt, model request/response (priced into model_calls), tool call/result, `run_tool`, hooks, human wait/input, interruption, error, pause/resume, session start/end | `tracer.py` |
| 17.9 | 10 new observe-only canonical events (`ModelRequest`, `ModelResponse`, `HookStart`, `HookEnd`, `HumanWait`, `HumanInput`, `Interruption`, `AgentError`, `AgentPause`, `AgentResume`) | `engine/events.py` |
| 17.9 | `POST /api/v1/events` (token, ≤500 per call, in `/openapi.json`) | `dashboard/api_ops.py` |
| 17.9 | JS/TS client, no deps, fails closed (tested with Node) | `sdk/ts/dinoguard.mjs`, `.d.ts` |
| 17.9 | LangChain callback, `traced_tools` (LlamaIndex/CrewAI/LangChain), Pydantic AI decorator, CrewAI step callback, Deep Agents middleware | `frameworks.py` |
| 17.10 | Adapters + installers: Devin, Factory Droid, Deep Agents Code, Antigravity, Pi (JS extension), OpenClaw (plugin folder); Hermes (printed snippet) | `adapters/claude_like.py`, `hermes.py`, `antigravity.py`, `plugin_bridge.py`, `more_install.py` |
| 17.10 | Devin/Droid payloads that arrive through Claude Code settings are recognised (`DEVIN_PROJECT_DIR`, `FACTORY_PROJECT_DIR`) | `adapters/__init__.py::sniff` |
| 17.11 | MCP write tools `ack_insight`, `resolve_insight`, `set_policy_mode`, `run_audit`: request → human yes → apply; approval bound to the exact arguments and single-use | `assistant/mcp_write.py` |
| 17.11 | `dinoguard mcp --http` (127.0.0.1, bearer token), MCP call log = tool name + ok only, `ask` tool, plain-words audit answers | `assistant/mcp_http.py`, `nl2sql.py` |
| 17.12 | `git stash create` checkpoint before allowed risky commands / large overwrites; `dinoguard undo`; shown on the trace | `undo.py` |

Sources for every adapter are in its `docs/adapters/<harness>.md`, with what's still unverified (mostly exact
file-path field names). Deep Agents Code fails open on hook errors (no option to change that); Hermes needs
`fail_closed: true`, which the printed snippet includes.

### Fixed while finishing earlier phases (2026-11-26)
- **Phase 3, daemon not running after a reboot.** Today's live stream had 11 guard-down denies (14:46–14:58 UTC),
  so your prompts were blocked until the daemon came back. Now a hook that finds no daemon running starts it once
  and retries. It still denies on failure, version skew, timeouts and bad tokens, and it never restarts a daemon
  you stopped with `dinoguard daemon stop`. `daemon.autostart=false` turns this off.
  The daemon also **starts on login** now: `dinoguard install` adds a Startup-folder shortcut (`pythonw -m
  dinoguard.daemon.server --login`, no window; LaunchAgent on macOS, XDG autostart on Linux), and uninstall removes
  it. `dinoguard daemon autostart|no-autostart` toggles it, and doctor shows it. A login start is skipped after a
  deliberate `daemon stop`. Enabled on this laptop 2026-11-26.
- **Phase 4, done-when on real traffic.** Replaying today's real hook stream (604 records from this live Claude Code
  session) through the runner state machine: walk 449, sprint 134, guard-down 11, shield 6, instruct 2, deny 2.
- **Phase 11, packaging.** A wheel builds cleanly (163 files, with migrations, dashboard assets, prices and the 3
  console scripts), installs into a fresh venv, and runs `dinoguard --version`. **Published to PyPI as
  `dinoguard` 0.1.0 on 2026-11-26** (wheel + sdist, both pass `twine check`), so `pipx install dinoguard` works.

## Phase 12 — Inventory gaps, local core ✅ (2026-11-26, CLAUDE.md §17.1–17.8)
Added after the 2026-11-26 feature inventory. Tests: `tests/test_inventory_data.py`, `tests/test_inventory_audit.py`,
and new `contract-should-not` cases in `tests/policies/cases.py`.

| § | Item | Where |
|---|---|---|
| 17.1 | Retention: `retention_days` (default 90, 0 = forever), `dinoguard prune [--days] [--dry-run]`, daily daemon prune, stream trimmed | `store/retention.py` |
| 17.1 | Extra redaction regexes (`redaction.extra_patterns`; bad regexes are skipped) and `redaction.store_outputs=false` | `secrets/scanner.py`, `store/writer.py` |
| 17.2 | Environment label (`DG_ENV` / `config.environment`) stored per session | `config.py`, migration `002` |
| 17.2 | Session filters: id, model, environment, event type, has-deny, min cost, min tokens, eval score | `/api/sessions`, Sessions page |
| 17.2 | Trace export (redacted JSON/JSONL): `dinoguard export`, `/api/sessions/{id}/export`, Trace page links | `analytics/export.py` |
| 17.2 | Compare two sessions or groups (`--by model|harness|environment|outcome`); new Compare page | `analytics/compare.py` |
| 17.3 | Error fingerprints + groups, tool analytics, policy analytics (incl. observe "would" counts) on Metrics | `analytics/errors.py`, `analytics/stats.py` |
| 17.4 | Audit goal / project / sessions / env / reference context; evidence carries `transcript:line` | `audit/replay.py` |
| 17.4 | Agent contract (`.dinoguard/contract.yaml`) in audits + built-in `contract-should-not` (off, observe) | `audit/contract.py`, `policies/builtin/contract.py` |
| 17.4 | `audit_runs` history + coverage (`dinoguard audit --history`, Audit page) | `audit/runs.py` |
| 17.4 | Recurrence: a resolved insight whose failure returns is reopened (comment "recurred") and alerted | `audit/runs.py` |
| 17.4 | `dinoguard audit suggest <id> --install` / "Install in observe" button | `cli/data.py`, `/api/findings/{id}/draft` |
| 17.5 | `eval new --from "plain words"` (LLM or templates), `eval rollback`, `eval results --by` | `evals/draft.py`, `evals/manage.py` |
| 17.6 | Pack sources: `owner/repo@tag`, `github:`, release URL, local folder; `--only`; `pack:policy`; `DG_NO_DOWNLOAD`; `DG_PACK_BASE_URL` | `packs/source.py` |
| 17.7 | Hook drift table (unknown events/tools), `VERIFIED_VERSION` per adapter, doctor warnings, capability table in `status` | `store/drift.py`, `cli/core.py` |
| 17.8 | Alert routes by severity (`alerts.routes`) | `alerts/engine.py` |

Notes / decisions:
- Built-ins now number 51 (`contract-should-not` added). It's the first built-in that defaults to **observe**.
- Recurrence fingerprints are the policy name (or `contract:<text>`). Insights made before migration 002 have no
  fingerprint and won't be reopened; they're matched by title so no duplicates are created.
- Retention deletes only sessions that ended (or went quiet) before the cutoff. Findings, insights, policy versions
  and evals are never pruned.
- The running daemon keeps old code in memory until restarted (`dinoguard daemon restart` from your terminal).
  Until then the daily prune and drift tracking only run in new processes.
- Phase 13 (§17.9–17.12) followed the same day; see above.

## Verification run (2026-11-25)

### Feature status
| # | Feature | Built | Tests passing | Notes |
|---|---|---|---|---|
| 1 | Pre-action hook on every tool call | yes | yes (`test_phase2_claude`, `test_install`) | live in this machine's Claude Code |
| 2 | Allow / instruct / deny | yes | yes (`test_engine`) | |
| 3 | ~50 built-in policies | yes (50) | yes (≥2 block + ≥2 allow each) | |
| 4 | Self-protection, always on | yes | yes | blocked the agent several times during this run |
| 5 | Fail-closed daemon + version check | yes | yes (`test_daemon`) | manual check 12 passed |
| 6 | Adapters + canonical events | 15 harnesses (see docs/adapters), each checked against current docs 2026-11-26 | yes | |
| 7 | Stop workflow gates | yes (off by default) | yes | |
| 8 | Secret sanitising in output + storage | yes | yes | verified the test key is `[REDACTED:stripe_key]` in the store |
| 9 | Loop / hallucination / drift / intent / destructive steering | yes | yes | loop warns at 3, stops at 5 |
| 10 | LLM reviewer, hard/reviewable | yes | yes, with a fake LLM | not run against a real endpoint |
| 11 | Dashboard: sessions, traces, decisions | yes | yes (`test_dashboard`) | test session trace shows all 15 decisions with reasons |
| 12 | Execution graph with sub-agents | yes | yes | |
| 13 | Latency p50/p95/p99, cost, context | yes | yes | prices and context windows fixed in this run |
| 14 | SQL queries + tiles | yes | yes | |
| 15 | Local audit + schedule + email | yes | yes | email not sent for real |
| 16 | Findings and insights | yes | yes | |
| 17 | Plain-English policy drafting | yes | yes (template path; LLM path with a fake) | |
| 18 | Backtest, test examples | yes | yes | |
| 19 | Versions, observe→enforce, rollback | yes | yes | |
| 20 | Evals (code, classifier, judge, worker) + backfill | yes | yes (LLM kinds with fakes) | |
| 21 | Sentiment | yes | yes | |
| 22 | Alerts + channels | yes | yes, with fake channels | no real Slack/email/WhatsApp sends |
| 23 | Assistant + MCP server | yes | yes | |
| 24 | Policy packs + checksum pinning | yes | yes (local folder) | GitHub download not network-tested |
| 25 | SDK + OpenTelemetry ingest | yes | yes | |
| ★ | Dino runner: terminal, web, floating notch | yes | yes (`test_runner_tokens`, `test_notch`) | |
| ★ | Fuel bar + token tips + budget stop | yes | yes | tips now on by default |
| ★ | WhatsApp/Telegram phone approval | yes | yes, with fakes | not set up on this machine |
| ★ | Secret blocking before write | yes | yes | |
| ★ | Loop hard-stop after N repeats | yes | yes | |

### Manual checklist (Step 2), replayed through the real hook as one session in `C:\dg-test`
| # | Action | Result | Notch |
|---|---|---|---|
| 1 | read app.js | allow | walk |
| 2 | `rm -rf junk` | deny `no-rm-rf` | deny |
| 3 | read `.env` / `cat .env` | deny `no-env-read` | deny |
| 4 | `sudo echo hi` | deny `no-sudo` | deny |
| 5 | `git push --force origin main` | deny `no-force-push` | deny |
| 6 | `DROP TABLE users` | instruct `warn-destructive-sql` | instruct |
| 7 | write config.js with an `sk_live_` key | deny `secret-before-write` | deny |
| 8 | `dinoguard uninstall` / edit Claude settings | deny `guard-self-protect` | shield |
| 9 | read app.js 4 more times | 3rd read: token tip (sprint); 4th–5th: `loop-detected` instruct | sprint → instruct |
| 10 | failing test run 5 times | runs 3–4: instruct; run 5: deny | instruct → deny |
| 11 | `git push origin main` | deny `no-push-main` (phone approval not set up) | deny |
| 12 | daemon down | deny, with a `dinoguard doctor` hint | guard down |

Before the fix, case 9 showed only the loop warning, because all token tips were off by default.
Run 11 needs `dinoguard approve setup` to show the blue "?".

### Fixed in this run
- **Cost was about 5× too high.** It used old Opus prices ($15/$75, $1.50 cache reads) instead of Opus 5.5 ($4/$20,
  $0.20 cache reads). It also assumed a 200k window for 1M-window models ("208k/200k"). The table is updated, an
  untouched old copy is upgraded automatically, and stored costs are recalculated.
- **Token tips were off by default**, so the orange sprint state never appeared in normal use.
- **Hook encoding (earlier in this run):** the hook now reads and writes UTF-8 regardless of the console code page.

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

### Floating notch (added 2026-11-25, on request)
- `dinoguard notch`: an always-on-top tkinter pill beside your apps (`#1F1F1E`, square top, 20 px rounded
  bottom corners, 230×46 → 330×104 with a 0.35 s ease), with the dino as round canvas dots, a fuel bar and a
  status line. It expands for 4 s on deny/tip/instruct/approval, then shrinks back. Deny/shield show a grey
  blinking dino behind a red/purple dotted wall.
- **New dino sprite** (22×13 body + 2 alternating leg rows) is used everywhere: terminal runner (now 4 braille
  rows tall), web runner, notch, and the regenerated `docs/runner.gif`.
- It is draggable, and its position is saved in `~/.dinoguard/notch.json`. Right-click menu: corner, dashboard,
  hide 10 min, quit. One instance at a time (OS file lock). It runs in the background by default.
- `--autostart` creates a Startup-folder shortcut (`.lnk` via PowerShell, with a `.vbs` fallback; LaunchAgent on
  macOS, `.desktop` on Linux). `--no-autostart` removes it.
- Measured on this laptop: **0.6% of the machine (4.5% of one core)** at 20 fps. Only changed canvas items are
  redrawn. All 9 states are rendered on a real Tk canvas in the tests.
- Fixed alongside it: the hook read stdin with the console code page (cp1252 on Windows) and wrote reasons the same
  way. A reason with a non-cp1252 character could crash the hook, and a crash fails open in Claude Code. It now reads
  and writes UTF-8 bytes and accepts a BOM.

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
- Published to PyPI 2026-11-26: `pipx install dinoguard`.

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
- Price table: current Claude API list prices (2026-11); cache writes use 1.25× input, and missing cache-read
  prices are estimated at 0.1× input. Subscription plans aren't billed per token, so the cost shown is "what it
  would cost on the API". Edit `~/.dinoguard/prices.yaml` to change it.
- Token tips (`token-repeat-read`, `token-big-output`, `token-context-high`, `token-model-fit`) are now on by
  default. They never block. `token-budget` stays off until you set a budget.

## Not done / open items (updated 2026-11-26)
- **Needs your accounts or credentials:**
  - real WhatsApp/Telegram/Slack/email delivery and the LLM reviewer/judge/drafting against a live endpoint
    (implemented, covered with fakes)
  - a networked pack install from a real GitHub release (tested from a local folder and with a mirror URL)
- **Field names still unverified** (common spellings are accepted, and `dinoguard doctor` reports hook drift):
  - Devin read/write/edit
  - Droid Edit/Read/Grep
  - OpenClaw read/write/edit
  - Goose text_editor
- **OpenCode 2.0 desktop** uses a new plugin API; the generated v1 plugin may need a V2 version there.
- **Codex** needs you to trust the hooks once with `/hooks` after installing its hooks.
- **Deep Agents Code** fails open on hook errors; that's a limit of that harness.
- **Done since the first build:**
  - installed into your real Claude Code settings (2026-11-25)
  - Phase 4's live-session check (2026-11-26)
  - Codex and OpenCode verified against current docs and source; Goose upgraded from a stub to real hooks
    (2026-11-26)
