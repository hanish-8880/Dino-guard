-- Phase 12 (CLAUDE.md §17): environment labels, audit runs + coverage, recurrence, hook drift, retention.
ALTER TABLE sessions ADD COLUMN environment TEXT;
ALTER TABLE findings ADD COLUMN fingerprint TEXT;
ALTER TABLE insights ADD COLUMN resolved_at TEXT;
ALTER TABLE insights ADD COLUMN fingerprint TEXT;
CREATE TABLE IF NOT EXISTS audit_runs (
  id TEXT PRIMARY KEY, audit_id TEXT, started_at TEXT, finished_at TEXT, sessions INTEGER, tool_calls INTEGER,
  findings INTEGER, coverage_json TEXT, goal TEXT, scope_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_runs_audit ON audit_runs(audit_id, started_at);
CREATE TABLE IF NOT EXISTS hook_drift (
  harness TEXT, harness_version TEXT, kind TEXT, value TEXT, first_seen TEXT, last_seen TEXT,
  count INTEGER DEFAULT 0, PRIMARY KEY (harness, kind, value)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_sessions_last ON sessions(last_ts);
CREATE INDEX IF NOT EXISTS ix_findings_fp ON findings(fingerprint);
