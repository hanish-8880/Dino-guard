# OpenCode adapter (partly unverified)

Read 2026-11-25: https://opencode.ai/docs/plugins/ and /docs/build/plugins (+ /server). Plugins live in
`.opencode/plugins/` or `~/.config/opencode/plugins/`. The V2 API exposes `ctx.tool.hook("execute.before", ...)`;
the V1 server example uses `"tool.execute.before"`. **Blocking by throwing an error is not documented.** The V2
`ctx.permission.hook("evaluate")` can set `event.effect = "deny"`, which may be the sturdier route later.

`dinoguard install --harness opencode` writes `dinoguard.js` (V1 style). It pipes each tool call to
`dinoguard-hook opencode` and throws on deny. Our wire format: `{"event","tool","args","output","sessionID","cwd"}`
→ stdout `{"decision","reason"}`.

| Plugin hook | Canonical | Can block? |
|---|---|---|
| tool.execute.before | PreToolUse | yes, if OpenCode honours the thrown error (unverified) |
| tool.execute.after | PostToolUse | observe |
