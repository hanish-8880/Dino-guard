# OpenClaw adapter

Verified 2026-11-26 against https://docs.openclaw.ai/plugins/hooks.md, /plugins/hooks/tool-policy.md,
/plugins/hooks/reference.md, /tools.md, /tools/exec.md and /tools/apply-patch.md (npm `openclaw` 2026.11.10).

OpenClaw's tool hooks are in-process TypeScript plugins (`api.on("before_tool_call", ...)`); there is no
shell-command tool hook. `dinoguard install --harness openclaw` writes a plugin folder in the DinoGuard home
(`integrations/openclaw/`: `index.js`, `openclaw.plugin.json`, `package.json`). Then link and enable it:

```
openclaw plugins install --link "<that folder>"
openclaw plugins enable dinoguard
```

| Canonical event | OpenClaw hook | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | before_tool_call | yes | returns `{block: true, blockReason}` |
| PostToolUse / PostToolUseFailure | after_tool_call | observe (can't undo) | — |

Fails closed twice over: the plugin blocks if `dinoguard-hook` can't run or answers garbage, and OpenClaw blocks a
tool when a `before_tool_call` handler throws or times out (15 s default).

Tools: `exec`→Bash (`command`, `workdir`), `apply_patch`→Edit (path parsed from `input`), `read` / `write` / `edit`,
`web_fetch`, `web_search`.

Unverified: the parameter names of `read` / `write` / `edit` (common spellings are accepted), no cwd in the hook
context (the plugin sends the gateway's working directory), and whether `registerTrustedToolPolicy` (runs before
ordinary hooks) is a better fit; it needs a manifest contract, so we use a normal hook for now. Prompt-level
context injection (`before_prompt_build`) isn't wired because its return shape isn't documented.
