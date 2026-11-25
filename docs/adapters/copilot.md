# GitHub Copilot adapter (CLI + cloud agent)

Verified 2026-11-25 against https://docs.github.com/en/copilot/reference/hooks-reference and the cloud-agent
"use hooks" how-to. The reference page was truncated and its preToolUse payload/outputs came from a summary, so
re-check those.

Install: `dinoguard install --harness copilot` writes `~/.copilot/hooks/dinoguard.json` (user) or
`.github/hooks/dinoguard.json` (project; the cloud agent reads it from the default branch, `bash` only).
Each hook runs `dinoguard-hook copilot <event>` because the camelCase payload does not name the event.

| Copilot event | Canonical | Can block? | deny | instruct |
|---|---|---|---|---|
| preToolUse | PreToolUse | yes | `{"permissionDecision":"deny","permissionDecisionReason"}` | – |
| permissionRequest (CLI) | PermissionRequest | yes | same | – |
| postToolUse / postToolUseFailure | PostToolUse(+Failure) | observe | `additionalContext` | `additionalContext` |
| userPromptSubmitted, sessionStart/End, agentStop, subagentStart/Stop, preCompact, errorOccurred | mapped | observe | – | subagentStart: `additionalContext` |

**Caveat:** Copilot treats a hook *timeout* as fail-open. DinoGuard sets `timeoutSec: 180`, and its own daemon
client gives up and denies after 30 s, well before Copilot does. Other non-zero exits count as deny.
