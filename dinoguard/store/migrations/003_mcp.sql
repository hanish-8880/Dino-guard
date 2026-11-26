-- Phase 13 (CLAUDE.md §17.11-12): MCP call log (tool name + success only) and undo checkpoints.
CREATE TABLE IF NOT EXISTS mcp_calls (id TEXT PRIMARY KEY, ts TEXT, tool TEXT, ok INTEGER, transport TEXT);
CREATE TABLE IF NOT EXISTS checkpoints (
  event_id TEXT PRIMARY KEY, session_id TEXT, ts TEXT, repo TEXT, ref TEXT, head TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_checkpoints_session ON checkpoints(session_id, ts);
