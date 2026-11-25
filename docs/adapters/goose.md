# Goose adapter (stub)

Read 2026-11-25: https://github.com/block/goose README. It documents no hook or pre-tool mechanism; it mentions MCP
extensions and ACP, where the client app receives "permission requests". The goose-docs.ai permissions page
returned no content.

There is nothing to install. To guard Goose, put DinoGuard on a path you control: an ACP client that answers
permission requests, or an MCP proxy in front of your extensions. Either can call `dinoguard-hook goose` (same wire
format as the SDK: `{"event","tool","input","session_id","cwd"}` → `{"decision","reason","policy"}`) or
`dinoguard.check(...)` from Python.
