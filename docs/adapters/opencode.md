# OpenCode adapter

Verified 2026-11-26 against https://opencode.ai/docs/plugins, /docs/build/plugins (+ /server) and the source at
tag v1.19.6 (2026-11-17; repo now `anomalyco/opencode`): `packages/plugin/src/index.ts`,
`packages/opencode/src/session/{tools,processor}.ts`, `plugin/index.ts`, `permission/index.ts`.

`dinoguard install --harness opencode` writes `dinoguard.js` into `.opencode/plugins/` (project) or
`~/.config/opencode/plugins/` (user). It pipes each tool call to `dinoguard-hook opencode` and **throws on deny**.
In v1.19.6 the `tool.execute.before` trigger runs before the tool with no try/catch, so a throw stops the tool,
and the model gets a tool error. If `dinoguard-hook` can't run or answers garbage, the plugin treats it as a deny.

| Plugin hook | Canonical | Can block? |
|---|---|---|
| `tool.execute.before(input{tool, sessionID, callID}, output{args})` | PreToolUse | yes (throw) |
| `tool.execute.after(input{tool, sessionID, callID, args}, output{title, output, metadata})` | PostToolUse | observe |

Wire format (ours): `{"event","tool","args","output","sessionID","callID","cwd"}` → `{"decision","reason"}`.

Tools: `bash` (`command`, `workdir`; also used for pwsh/cmd), `read` / `edit` / `write` (`filePath`), `apply_patch`
(`patchText`; the file is read from the patch header), `grep`, `glob`, `webfetch`, `websearch`, `task`.

Known limits:
- `permission.ask` is declared in the v1 plugin types but nothing calls it in v1.19.6, so it isn't used.
- For `task` subtasks, a throwing before-hook ends the subtask without a tool-error result.
- The optional OpenCode 2.0 desktop moves plugins to a V2 API (`Plugin.define`, `ctx.tool.hook("execute.before")`,
  `ctx.permission.hook("evaluate")`). Its release notes say v1 plugins need an update, so the generated plugin may
  stop loading there. A V2 plugin is not written yet; `dinoguard doctor` will show no OpenCode events if that happens.
