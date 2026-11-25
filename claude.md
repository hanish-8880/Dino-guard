# CLAUDE.md — DinoGuard

> Working name: **DinoGuard** (rename freely). A from-scratch AI agent guardrail + observability platform.
> Architecture is *inspired by* FailproofAI. **Do not copy any FailproofAI code, names, logos, rule names or text.**
> Their license is MIT + Commons Clause. We study the ideas only and write 100% original code.

---

## References (read for ideas only)

Use these to understand **how FailproofAI approaches the problem**: their architecture, event model, policy semantics, CLI flow and docs structure. Read them when a section below is unclear or when you design something new.

- Main repo: https://github.com/FailproofAI/failproofai
- Policy pack repo: https://github.com/FailproofAI/policies
- Website (features, pricing): https://befailproof.ai/
- Docs index (all pages): https://docs.befailproof.ai/llms.txt
- Key docs pages:
  - Concepts: https://docs.befailproof.ai/start/concepts.md
  - Harnesses + events: https://docs.befailproof.ai/reference/harnesses.md
  - Policy SDK: https://docs.befailproof.ai/reference/policy-sdk.md
  - Policies overview: https://docs.befailproof.ai/policies/overview.md
  - Failure behavior (fail-closed): https://docs.befailproof.ai/policies/failure-behavior.md
  - Authority (hard/reviewable): https://docs.befailproof.ai/policies/authority.md
  - Jev reviewer: https://docs.befailproof.ai/policies/jev.md
  - Packs: https://docs.befailproof.ai/policies/packs.md
  - Local audit: https://docs.befailproof.ai/audits/local-audit.md
  - Alerts: https://docs.befailproof.ai/audits/alerts.md
  - Evaluations: https://docs.befailproof.ai/evaluations/overview.md
  - Failproof agent (ask your data): https://docs.befailproof.ai/sessions/assistant.md

**Rules for using the references:**
1. Read to learn the *approach and behaviour*. Then close it and write **our own code from scratch**, in Python.
2. **Never copy or paste code, file structure, function bodies, rule names, docs text, prompts or tests** from their repos. Don't translate their TypeScript line by line into Python.
3. If our design differs from theirs, this CLAUDE.md wins.
4. If you learn something new from their repo that's missing here, add it to `docs/notes-from-reference.md` in your own words, with the link. Then ask before changing the plan.
5. License is MIT + Commons Clause. Keep a line in our README: "Architecture inspired by FailproofAI. No code reused."

---

## 0. How to work on this repo (instructions for Claude Code)

- Build in the **phases in section 14**, in order. Do not start a phase until the previous phase's "Done when" checks pass.
- After every phase: run `pytest -q`, fix failures, then update `PROGRESS.md` (create it) with what's done.
- Keep every module small and testable. No file over ~400 lines.
- **Never weaken the guard to make a test pass.** If a safety test fails, fix the code, not the test.
- Before writing a hook adapter, **check the harness's current hook docs** (formats change between versions). Write what you verified into `docs/adapters/<harness>.md`.
- Prefer the standard library. Allowed deps: `fastapi`, `uvicorn`, `pydantic`, `rich`, `httpx`, `pyyaml`, `watchfiles`, `pytest`. Ask before adding others.
- All times are stored in UTC ISO-8601. All IDs are ULIDs or UUID4.
- Everything runs **locally on one laptop**. No cloud account needed. Network calls only for: optional LLM reviewer/judge, optional alerts (WhatsApp/Telegram/Slack/email/webhook).

---

## 1. What we are building (one paragraph)

AI coding agents (Claude Code, Cursor, Codex…) run commands and edit files on your laptop. DinoGuard sits between the agent's harness and the real action. **Before** every action it checks rules and answers **allow / instruct / deny**. It records everything, shows a live **dotted dino runner** in the terminal, a local **dashboard**, audits history to find risky patterns, drafts new rules, tests rules on past traffic, scores finished sessions, and sends alerts or **asks the human on WhatsApp** for risky actions. It also watches **token burn** and coaches the agent to use fewer tokens.

---

## 2. Architecture

```
 ┌──────────────┐   native hook JSON    ┌──────────────┐   canonical event    ┌───────────────────┐
 │  Harness      │ ───────────────────▶ │   Adapter     │ ──────────────────▶ │  dinoguardd        │
 │ (Claude Code, │                       │ (per harness) │   (unix socket)     │  (daemon)          │
 │  Cursor, ...) │ ◀─────────────────── │               │ ◀────────────────── │  policy engine     │
 └──────────────┘  native decision       └──────────────┘   decision           │  + event store     │
                                                                               └─────────┬─────────┘
                                                                                         │ writes
                                       ┌───────────────┬────────────────┬────────────────┼───────────────┐
                                       ▼               ▼                ▼                ▼               ▼
                                  SQLite store    Dino runner      Dashboard        Alerts/Approve   Reviewer
                                  (~/.dinoguard/  (terminal pane,  (localhost:8030) (WhatsApp,       (LLM, optional)
                                   events.db)      reads stream)                     Slack, email)
```

Key ideas (keep these exactly):

1. **Hook → Adapter → Canonical event → Policy engine → Decision → Adapter → Native response.**
2. **Adapters normalise** harness-specific event names, tool names and tool-input fields into one canonical shape *before* any policy runs. Policies never see harness-specific formats.
3. **Daemon (`dinoguardd`)** is the single evaluator once configured. The hook process is a thin client that talks to it over a unix socket (`~/.dinoguard/dinoguardd.sock`).
4. **Fail-closed:** if the daemon is configured but unreachable, times out, or its protocol version ≠ client version → **deny**, with a reason telling the operator to run `dinoguard doctor`. No silent fallback to a second evaluator. (Before a daemon is configured, the hook evaluates in-process.)
5. **Capture vs enforcement:** some events can block (pre-tool, prompt submit, permission request, stop), others are observe-only (post-tool, session lifecycle). Blocking after a tool ran **cannot undo side effects** — document this in the UI.

---

## 3. Repo layout

```
dinoguard/
  CLAUDE.md
  PROGRESS.md
  pyproject.toml                 # console scripts: dinoguard, dinoguardd
  dinoguard/
    __init__.py                  # public SDK: policy, allow, instruct, deny, check()
    version.py                   # __version__, PROTOCOL_VERSION
    cli/                         # Typer-free: argparse subcommands (see §11)
    hook/client.py               # thin hook entrypoint called by harnesses
    adapters/
      base.py                    # Adapter interface
      claude_code.py             # Phase 1
      cursor.py codex.py copilot.py gemini.py opencode.py goose.py   # Phase 7 (stubs first)
      sdk.py                     # for custom agents / LangGraph / CrewAI
    daemon/server.py             # unix-socket server, health, version handshake
    engine/
      events.py                  # canonical event + tool enums
      context.py                 # PolicyContext
      decisions.py               # allow/instruct/deny + merge rules
      registry.py                # load built-ins, packs, project, user policies
      runner.py                  # evaluate with deadlines, first-deny-wins
      authority.py               # hard / reviewable
      modes.py                   # observe / enforce per policy+deployment
    policies/builtin/            # one file per category (see §6)
    reviewer/                    # LLM reviewer ("checks"), §7
    store/                       # SQLite schema, migrations, queries
    tokens/                      # transcript reader, cost table, coaching rules
    secrets/                     # secret scanner patterns + entropy
    audit/                       # history replay, findings, insights
    authoring/                   # plain-English → policy draft, backtest
    deploy/                      # versions, rollout, rollback
    evals/                       # code evals, LLM judge, sentiment
    alerts/                      # rules + channels (whatsapp, telegram, slack, email, webhook)
    approvals/                   # phone approval flow
    packs/                       # pack install/show/verify (sha256)
    runner_ui/                   # dotted dino terminal runner (rich)
    dashboard/                   # FastAPI app + static HTML/JS
    assistant/                   # "ask your data" chat + MCP server
    otel/                        # OpenTelemetry ingest (optional)
  tests/
  docs/
```

---

## 4. Canonical events

Adapters map native events onto this list (superset; not every harness emits all):

| Group | Events | Can block? |
|---|---|---|
| Tools | `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` | Pre: yes. Post: observe (or "replace result" where harness allows) |
| Permissions | `PermissionRequest`, `PermissionDenied` | Request: yes |
| Prompts | `UserPromptSubmit`, `UserPromptExpansion` | yes (deny or inject instruction) |
| Turn end | `Stop`, `SubagentStop`, `StopFailure` | yes — "not done yet, continue" gates |
| Sessions | `SessionStart`, `SessionEnd`, `Setup` | observe |
| Subagents/tasks | `SubagentStart`, `TaskCreated`, `TaskCompleted`, `TeammateIdle` | observe |
| Context | `PreCompact`, `PostCompact`, `InstructionsLoaded` | PreCompact: yes |
| Environment | `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove` | observe |
| Interaction | `Notification`, `Elicitation`, `ElicitationResult` | observe |

Canonical tools: `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Task`, `MCP:<server>:<tool>`, `Other`.
Canonical tool input fields: `Bash.command`, `Read.file_path`, `Write.file_path, content`, `Edit.file_path, old_string, new_string`, `Grep.pattern, path`.

Tool classes (used by the reviewer): `shell`, `write`, `read`, `network`, `other`.

### Canonical event JSON

```json
{
  "id": "evt_...", "ts": "2026-11-25T09:30:00Z",
  "harness": "claude", "harness_version": "x.y.z",
  "event": "PreToolUse",
  "session": {"id": "...", "cwd": "/path", "transcript_path": "...", "permission_mode": "default"},
  "tool": {"name": "Bash", "class": "shell", "input": {"command": "rm -rf src"}},
  "prompt": null,
  "raw": { "...native payload..." : "kept for debugging" }
}
```

---

## 5. Policy engine (exact semantics)

### Writing a policy (Python, our own API)

```python
from dinoguard import policy, allow, instruct, deny

@policy(
    name="no-generated-edits",
    description="Change generated files through their generator",
    events=["PreToolUse"],          # omit = all events
    category="workflow",
    authority="hard",               # or "reviewable"
    reviewed_by=[],                 # reviewer check names, required if reviewable
    default_enabled=True,
    params={"dirs": ["dist", "generated"]},
)
def no_generated_edits(ctx):
    if ctx.tool_name not in ("Write", "Edit"):
        return allow()
    path = str(ctx.tool_input.get("file_path", ""))
    if any(f"/{d}/" in f"/{path}" for d in ctx.params["dirs"]):
        return deny("Edit the source and run the generator instead.")
    return allow()
```

`PolicyContext` fields: `event_type`, `tool_name`, `tool_class`, `tool_input`, `payload`, `session` (id, cwd, transcript_path, permission_mode, harness meta), `harness`, `params`, `history` (read-only helper: last N events this session), `tokens` (token stats for session).

Decision helpers: `allow(reason=None)`, `instruct(reason)`, `deny(reason)`. Reasons are written **for the agent**: what was detected + what to do instead.
Rule: **never use `instruct` for a safety boundary** — instruct delivery varies by harness. Safety = `deny`.

### Evaluation order
1. Load order: explicit `--policies` files → project `.dinoguard/policies/*policies.py` → user `~/.dinoguard/policies/*policies.py` (alphabetical; a file found twice loads once) → installed packs.
2. **Built-ins run before custom policies.**
3. **First `deny` stops evaluation.** Multiple `instruct` results are **combined** if nothing denies.
4. Each policy function has a **10 s deadline**. Exceptions/timeouts → logged, treated as `allow` (except the self-protection guard, which is never skippable).
5. Module load deadline 10 s; a file that fails to load is skipped and reported in `dinoguard policies`.
6. Policy names must be unique across built-in, custom, pack and deployed policies.

### Modes
- Every enabled policy runs in **observe** (record "would deny/instruct", don't enforce) or **enforce**.
- New policies default to **observe**. Promote with `dinoguard policies enforce <name>`.

### Authority
- `hard` (default): verdict stands. The reviewer can never clear it.
- `reviewable`: the LLM reviewer may clear a deny/instruct, but only by asking **every** check in `reviewed_by`; none may answer deny (a warn still clears).
- Malformed authority declarations → treated as `hard` (fail closed).
- `guard-self-protect` is **always on, always hard**, cannot be disabled or made reviewable.

### Pack load failure (narrow deny)
If an **enforced** pack fails to load, or loads fewer policies than its manifest declares: deny **only** events/tools those missing policies matched, attributed to `pack/pack-unavailable`, taking precedence. Exceptions: observe packs, disabled policies, active session pause, load timeouts (transient). On `UserPromptSubmit` → instruct instead of deny, so the user can still talk to the agent to fix it.

---

## 6. Built-in policies (our own names; ~50)

`D` = default enabled. `R` = reviewable (others hard). Events default `PreToolUse` unless noted.

**Self-protection**
- `guard-self-protect` D (alwaysOn): deny agent commands/edits that disable or uninstall DinoGuard, edit its hooks in harness settings, kill the daemon, or edit `~/.dinoguard/`.

**Secrets**
- `secret-in-output` D (PostToolUse): scan tool output for API keys, JWTs, private keys, passwords; redact in the store, instruct agent not to repeat them.
- `secret-before-write` D: scan `Write.content` / `Edit.new_string` / Bash heredocs for secrets → deny ("use an env var"). **(our extra — blocks before the write)**
- `no-private-key-write`: deny writing `id_rsa`, `*.pem`, `*.key`.

**Environment**
- `no-env-read` D: deny reading `.env*` (allow `.env.example`).
- `no-env-write`: deny writing `.env*`.
- `no-env-dump` R: deny `env`, `printenv`, `set`, `export -p`.
- `no-read-outside-project` R: deny reads outside `cwd` (allowlist configurable).

**Dangerous commands**
- `no-sudo` D, `no-rm-rf` D (recursive force delete), `no-curl-pipe-shell` D (`curl|sh`, `wget|bash`), `no-chmod-777` R, `no-disk-destroy` (`mkfs`, `dd of=/dev/`, `:(){ :|:& };:`).

**Infrastructure**
- `no-kubectl-mutate` R (apply/delete/scale/patch), `no-terraform-apply` R (apply/destroy), `no-cloud-cli-mutate` R (aws/gcloud/az create/delete/update), `no-helm-mutate` R, `no-ci-trigger` R (`gh workflow run`, `gh run rerun`).

**Git**
- `no-push-main` D, `no-force-push` D, `no-commit-on-main` R, `no-amend-pushed` R, `no-stash-drop` R, `no-git-add-all` R (`git add -A`/`.`), `no-git-clean` R (`git clean -fdx`).

**Database**
- `warn-destructive-sql` D (instruct): `DROP`, `TRUNCATE`.
- `no-delete-without-where`: `DELETE FROM x;` with no `WHERE`.
- `warn-schema-change` R (instruct): `ALTER TABLE`, migrations on prod URLs.

**Packages & system**
- `no-package-publish` (npm/pypi/cargo publish), `no-global-install` R, `use-repo-package-manager` (instruct: lockfile says pnpm/yarn/uv/poetry → use it), `warn-large-write` (instruct: >N KB), `warn-background-process` R (instruct: `&`, `nohup`, `disown`).

**AI behaviour (failure modes — respond with "steer" = instruct with a concrete alternative)**
- `loop-detected` D: same tool + same input ≥3 times in last 10 calls, or same failing command ≥3 times → instruct "summarise progress, try a different approach"; at ≥5 → deny and ask the human. **(stop part is our extra)**
- `tool-not-found` (hallucination): calls to non-existent tool, file or path → deny + suggest nearest existing names (fuzzy match on directory listing / tool registry).
- `goal-drift`: compare action with original user prompt (cheap keyword/embedding check, optional reviewer) → instruct with the original goal.
- `intent-complete` (Stop/PostToolUse): if the task's success signal is met (tests pass, requested file exists) → instruct "task is done, report result, stop".
- `destructive-command`: catch-all for destructive shapes not covered above → deny + suggest safe alternative (e.g. `git stash` instead of `git checkout -- .`).

**Workflow gates (Stop / SubagentStop)** — block the agent from ending its turn until:
- `stop-require-commit`, `stop-require-push`, `stop-require-pr`, `stop-require-no-conflicts`, `stop-require-green-ci`, `stop-require-tests-pass`. All off by default.

**Token coaching (our extras, instruct only, never block)**
- `token-repeat-read`: same file read ≥3 times this session → tip.
- `token-big-output`: tool output > 20k tokens → tip "search/grep instead".
- `token-context-high`: context ≥80% of window → tip "/compact".
- `token-model-fit`: tiny edits on the largest model for many turns → tip "use a smaller model".
- `token-budget` (configurable): session cost > budget → instruct at 80%, deny at 100% until user approves.

**Human approval (our extra)**
- `phone-approval`: for configured patterns (default: push to main, prod DB, `terraform apply`) → hold and send WhatsApp/Telegram "Approve? YES/NO". Wait up to `timeout_s` (default 120). YES → allow, NO/timeout → deny.

---

## 7. LLM reviewer ("checks") — for unclear cases

- Runs only at `PreToolUse` / `PermissionRequest`, only when a **reviewable** policy fired or a check's `applies_to` matches.
- Compares the gated tool call with the **user's original request** (first prompt + latest prompt of the session).
- Check declaration:
  ```python
  check(name="payments-outside-task", title="Touched payments outside the task",
        applies_to=["shell","network","write"], mode="deny",  # deny: block on strong, warn on moderate evidence; "instruct": warn only
        user_can_override=True,
        probes=[{"id":"p1","instructions":"Does the call touch payment systems?","criteria":{"true":"...","false":"..."}}],  # 1–6 probes, all must hold
        exempt={"instructions":"Did the user explicitly ask for payments work?"},
        precondition="always",  # always|protected_branch|in_git_repo|has_paths|paths_outside_project|system_or_root_paths
        guidance="Stay on the login task. Ask the user before touching payments.")
  ```
- Reviewer **observe** mode records what it would decide; **enforce** applies it. Default observe.
- Hard policies always have final say. Reviewer may also warn/deny on its own. If the reviewer errors/times out (default 8 s) → the policy result stands.
- Provider: any OpenAI-compatible endpoint (`DG_REVIEWER_URL`, key via stdin/keyring). Cache verdicts by hash(tool call + prompt) for the session.
- Commands: `dinoguard reviewer setup --url ... --mode observe`, `dinoguard reviewer status`, `dinoguard reviewer test`.

---

## 8. Storage (SQLite, `~/.dinoguard/events.db`, WAL mode)

Tables (create migrations in `store/migrations/`):
- `sessions(id, harness, cwd, started_at, ended_at, first_prompt, model, status, cost_usd, tokens_in, tokens_out, tokens_cache)`
- `events(id, session_id, ts, event, tool_name, tool_class, input_json, output_excerpt, duration_ms, error, parent_id, agent_id)` — `parent_id/agent_id` for sub-agent tree
- `decisions(id, event_id, policy, version, mode, decision, reason, authority, reviewer_verdict, latency_ms)`
- `model_calls(id, session_id, ts, model, tokens_in, tokens_out, cache_read, cost_usd, latency_ms)` (from transcript)
- `policies(name, source, version, enabled, mode, authority, params_json, code_hash)`
- `policy_versions(name, version, code, created_at, notes)` immutable
- `deployments(id, policy, version, mode, scope, created_at, rolled_back_from)`
- `audits(id, goal, scope_json, cadence_days, last_run, next_run)`
- `findings(id, audit_id, failure_mode, severity, evidence_json, session_ids)`
- `insights(id, source, finding_id, alert_id, status[open|ack|assigned|resolved], owner, comments_json)`
- `alerts(id, name, description, enabled, trigger_kind, trigger_spec_json, severity, channels_json, check_interval_s, consecutive_checks)`
- `alert_firings(id, alert_id, ts, value, insight_id)`
- `evals(id, name, kind[code|judge|classifier|worker], spec, condition, version, enabled)`
- `eval_results(id, eval_id, version, session_id, score, passed, metric, unit, reasoning)`
- `approvals(id, event_id, channel, sent_at, answered_at, answer)`
- `packs(publisher, name, tag, digest, manifest_json, selection_json, installed_at)`
- `chats(id, title, created_at)`, `chat_messages(...)` for assistant

Also append every event as one JSON line to `~/.dinoguard/stream.jsonl` (the dino runner tails it).
Secrets are **redacted before storage**.

---

## 9. Tokens and cost

- Read the harness transcript (`session.transcript_path`, JSONL for Claude Code) incrementally (remember byte offset). Each assistant message carries usage: input, output, cache read/write tokens + model.
- `tokens/prices.yaml`: per-model $/1M tokens (user-editable; don't hardcode in code).
- Track per session: total tokens, burn rate (tokens/min over last 60 s), context % of window, cost.
- Feed `ctx.tokens` to policies and to the runner.

---

## 10. Dotted dino runner (our signature feature)

`dinoguard runner` — a thin terminal strip (8–10 rows) for a split pane (tmux / VS Code terminal). Built with `rich.live`. Draw with braille/dot glyphs (`⣿⠿⣀·`). Original character — **not** Google's T-Rex.

States (map from latest decision/event):

| State | Trigger | Look |
|---|---|---|
| walk (green) | allow | dino walks, ground dots scroll |
| sprint (orange) | burn rate high / token tip | faster, speed lines, tip bubble |
| instruct (yellow) | instruct | dashed yellow wall, dino slows |
| deny (red) | deny | solid red wall, dino stops & blinks, reason under it |
| shield (purple) | guard-self-protect | purple wall |
| review (purple scan) | reviewer running | purple dots scanning |
| wait (blue ?) | phone approval pending | blinking "?" above head |
| ghost (grey dashed) | observe-mode would-deny | grey wall, dino walks through |
| guard down (red bg) | daemon unreachable | whole strip red, everything denied |

Bottom line: `tokens 42k/200k ▕████░░░░▏ burn: low · $0.31 · last: Bash npm test allow`.
Also `--web` flag: same animation on a `<canvas>` at `localhost:8030/runner`.
Must render at ≥20 fps with <3% CPU. Reconnects if the stream file rotates.

---

## 11. CLI (all subcommands)

```
dinoguard install [--harness claude|cursor|codex|...] [--scope user|project|local]   # writes hooks, starts daemon
dinoguard uninstall
dinoguard doctor            # daemon health, version skew, hook wiring, pack integrity
dinoguard status
dinoguard runner [--web]
dinoguard dashboard          # opens localhost:8030
dinoguard policies [list|enable|disable|observe|enforce|show <name>]
dinoguard policies add <owner>/<repo>[@tag] [--policy x] [--category y] [--all]
dinoguard policies show <owner>/<repo>    # reads manifest only, never runs pack code
dinoguard policies remove <publisher/name>
dinoguard policy new "never touch the payments folder"   # plain English → draft
dinoguard policy test <name>               # should-block + should-allow examples
dinoguard policy backtest <name> [--days 30]
dinoguard policy publish <name>            # immutable version
dinoguard policy deploy <name> --version N --mode observe|enforce
dinoguard policy rollback <name>
dinoguard audit [--schedule DAYS (1–90, default 7)] [--email addr] [--status] [--no-schedule]
dinoguard eval [new|test|deploy|backfill|results]
dinoguard alerts [new "tell me when ..."|test <id>|list]
dinoguard approve setup --whatsapp|--telegram
dinoguard reviewer [setup|status|test]
dinoguard ask "which sessions burned the most tokens this week?"
dinoguard pause [--minutes N]     # session pause (pack-unavailable deny doesn't fire)
dinoguard publish                 # build a pack from local policies
```

Env: `DG_NO_FIRST_RUN=1` skips the first-run wizard. `dinoguard install` must work non-interactively in CI.

---

## 12. Dashboard (`localhost:8030`, FastAPI + plain HTML/JS, no build step)

Pages:
1. **Home** — today: actions, allowed/instructed/denied, cost, tokens, open insights; chat box "ask anything about your agents".
2. **Sessions** — list (filter harness, project, status, date), each → **trace**.
3. **Trace** — ordered + nested view (model calls, tool calls, hook decisions, errors, human actions), **execution graph** with sub-agents, what was blocked and the exact message the agent received. Replay button (step through).
4. **Live** — live event stream + the web dino runner.
5. **Policies** — activity (which policy matched, why), editor (code + plain-English draft), versions/library (diff), backtest results, enforcement (observe/enforce per policy), packs.
6. **Audit** — sessions, tool calls, projects, policy hits, frequent findings → drill into project/session.
7. **Findings & insights** — acknowledge, assign, comment, resolve.
8. **Evaluations** — list, test, deploy, versions, score charts over time, compare agents.
9. **Alerts** — "tell me when …" builder, preview "would have fired N times in last 24h", channels, test notification.
10. **Metrics** — p50/p95/p99 latency for models, tools, hooks; cost per model; context-window usage; error rates.
11. **Queries** — read-only SQL box over the store (SELECT only, timeout 5 s), save query as dashboard tile.
12. **Settings** — reviewer, approvals channel, prices, budgets, alert channels.

Shortcut: `Ctrl+J` opens the assistant dock on any page.

---

## 13. Other subsystems

**Audit (local history replay)** — scan local histories of every supported harness found on disk (start: Claude Code `~/.claude/projects/**.jsonl`), replay tool activity through built-in policies, report risky or wasteful behaviour (policy hits, loops, secret exposure, token waste). Group into **findings** (failure mode, severity, evidence, affected sessions). Output is "evidence for review, not proof". Each finding offers **"Draft a policy"**.

**Policy authoring** — plain English (or a finding) → LLM drafts Python policy + 2 should-block and 2 should-allow examples → user reviews → test → backtest → publish → deploy observe → enforce. Without an LLM key, fall back to a template picker (path / command / regex).

**Backtest** — replay a draft against stored past events; report "would have blocked N, would have interrupted M working calls", list them.

**Versions & rollback** — every publish = new immutable version. Rollback = redeploy last good version.

**Evaluations** (run when a session ends; conditions limit which sessions):
- `code`: one safe Python expression over the session dict (no imports, no network; evaluate with a restricted AST evaluator) → score 0–1 / metric+unit / assertion.
- `classifier`: small model, score only.
- `judge`: LLM, score + reasoning.
- `worker`: user's own process polls finished sessions over HTTP and posts results.
- Test (not stored) → deploy (immutable version) → scores only new sessions; `backfill` for old ones.
- **Sentiment**: built-in eval flagging frustrated/angry user prompts.

**Alerts** — conditions: errors, eval scores, eval combos, custom SQL, metric thresholds (`trigger_kind`, `trigger_spec{metric, op, value, window_s}`), severity warning/critical, check interval + consecutive checks. Firing opens an **insight**. Channels: WhatsApp, Telegram, Slack webhook, email (SMTP), signed webhook (HMAC-SHA256 header).

**Packs** — a pack = GitHub release with `dinoguard-pack.json` manifest (policies, categories, defaults, authority per policy, `min_cli_version`), entry file, `SHA256SUMS`. Install: verify checksums, record digest, pin tag; re-verify digest before every load (retag → refuse to load). Packs can't escalate: names can't contain `/`, are prefixed with the pack name, can't mark built-ins reviewable. `show` reads manifest only. `SHA256SUMS` is integrity, **not** a signature — say so in output.

**Assistant ("ask your data")** — chat (dashboard + `dinoguard ask`) that turns questions into read-only SQL over the store, shows the SQL and a table/chart, cites session IDs. Treat answers as investigation aids. Also expose an **MCP server** (`dinoguard mcp`) with tools: `list_sessions`, `get_trace`, `query`, `list_findings`, `explain_decision`, so an agent can read its own DinoGuard data.

**SDK for custom agents** — `from dinoguard import check; decision = check(tool="Bash", input={...}, session_id=...)` + helpers for LangGraph, CrewAI, LlamaIndex, Pydantic AI. Optional OpenTelemetry ingest endpoint.

---

## 14. Build phases (do in order)

**Phase 1 — Hook + log (Claude Code)**
- `dinoguard install` writes into `~/.claude/settings.json` (merge, don't clobber; back up first):
  `{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"dinoguard-hook claude"}]}], ...same for PostToolUse, UserPromptSubmit, Stop, SubagentStop, SessionStart, SessionEnd, PreCompact, Notification}}`
- Hook reads stdin JSON → adapter → canonical event → append to `stream.jsonl` + SQLite.
- Done when: running Claude Code produces one row per action; `uninstall` restores settings exactly.

**Phase 2 — Policy engine + core rules**
- engine (§5), self-protect, secrets, env, dangerous, git, database rules.
- Claude Code response mapping: deny → exit code 2 with reason on stderr (or JSON `hookSpecificOutput.permissionDecision: "deny"` + reason — verify current docs); instruct → allow + inject context where supported; Stop gate → `{"decision":"block","reason":...}`.
- Done when: tests prove `rm -rf src` denied, `cat .env` denied, `git push --force` denied, `DROP TABLE` instructed, `ls` allowed, disabling the guard denied.

**Phase 3 — Daemon + fail-closed**
- `dinoguardd` unix socket, version handshake, `doctor`, `status`. Kill daemon → every action denied with clear reason.

**Phase 4 — Dino runner + tokens**
- §9 + §10 + token coaching rules + loop-detected.
- Done when: a live Claude Code session animates correctly through allow / instruct / deny / sprint.

**Phase 5 — Dashboard v1**
- Home, Sessions, Trace (with sub-agent graph), Live, Policies (activity + enforcement), Metrics, Queries.

**Phase 6 — Approvals + alerts**
- WhatsApp (Twilio/Meta Cloud API or AiSensy) + Telegram bot; alerts engine + insights.

**Phase 7 — Reviewer + failure-mode policies**
- §7 reviewer, tool-not-found, goal-drift, intent-complete, destructive-command steering.

**Phase 8 — Audit, authoring, backtest, versions**
- §13 audit/findings/insights, plain-English drafting, backtest, publish/deploy/rollback, observe→enforce.

**Phase 9 — Evals + sentiment + assistant + MCP**

**Phase 10 — Packs + more adapters + SDK**
- Cursor, Codex, Copilot, Gemini CLI, OpenCode, Goose adapters (each with `docs/adapters/*.md` capability table: which events block vs observe). Python SDK + framework helpers.

**Phase 11 — Showcase polish**
- One-command install (`pipx install dinoguard && dinoguard install`), first-run wizard, `demo/` script that runs a scripted risky session to show every runner state in 60 s, README with GIF.

---

## 15. Tests that must always pass

- Each built-in policy: ≥2 should-deny and ≥2 should-allow cases (`tests/policies/`).
- Engine: first-deny-wins, instruct merge, 10 s timeout → allow, self-protect never skippable, malformed authority → hard.
- Fail-closed: daemon down / version skew → deny.
- Pack: tampered asset → refuse load; enforced pack missing → narrow deny; prompt submit → instruct.
- Secrets never stored unredacted.
- `install` then `uninstall` leaves harness settings byte-identical.
- Runner renders every state without crashing on narrow (40 col) terminals.

---

## 16. Feature checklist (theirs → ours, plus extras)

| # | Feature | Phase |
|---|---|---|
| 1 | Pre-action hook on every tool call | 1 |
| 2 | Allow / instruct / deny | 2 |
| 3 | Built-in policies across 9 categories (~50) | 2,4,7 |
| 4 | Self-protection, always on | 2 |
| 5 | Fail-closed daemon + version check | 3 |
| 6 | Adapters for many harnesses, canonical events | 1,10 |
| 7 | Turn-end (Stop) workflow gates | 2 |
| 8 | Secret sanitising in output | 2 |
| 9 | Loop / hallucination / drift / intent-complete / destructive steering | 4,7 |
| 10 | LLM reviewer with hard/reviewable authority | 7 |
| 11 | Local dashboard: sessions, traces, decisions | 5 |
| 12 | Execution graph with sub-agents | 5 |
| 13 | Latency p50/p95/p99, cost, context tracking | 5 |
| 14 | SQL queries + dashboard tiles | 5 |
| 15 | Local audit + schedule + email | 8 |
| 16 | Findings and insights workflow | 8 |
| 17 | Plain-English policy drafting | 8 |
| 18 | Backtest, test examples | 8 |
| 19 | Versions, observe→enforce deploy, rollback | 8 |
| 20 | Evaluations (code, classifier, judge, worker) + backfill | 9 |
| 21 | Sentiment analysis | 9 |
| 22 | Alerts (errors, scores, SQL, thresholds) + channels | 6 |
| 23 | Assistant "ask your data" + MCP server | 9 |
| 24 | Policy packs with checksum pinning | 10 |
| 25 | SDK for custom agents + OpenTelemetry | 10 |
| ★ | **Dotted dino runner (terminal + web)** | 4 |
| ★ | **Live token fuel bar + coaching tips + budget stop** | 4 |
| ★ | **WhatsApp/Telegram phone approval** | 6 |
| ★ | **Secret blocking before write** | 2 |
| ★ | **Loop hard-stop after N repeats** | 4 |

Out of scope for the showcase: multi-user orgs, RBAC, SSO/SAML, hosted cloud, SOC 2.    