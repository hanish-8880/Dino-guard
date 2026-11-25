CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, harness TEXT, cwd TEXT, started_at TEXT, ended_at TEXT, first_prompt TEXT,
  model TEXT, status TEXT DEFAULT 'active', cost_usd REAL DEFAULT 0, tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, tokens_cache INTEGER DEFAULT 0, transcript_path TEXT, last_ts TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, session_id TEXT, ts TEXT, event TEXT, tool_name TEXT, tool_class TEXT,
  input_json TEXT, output_excerpt TEXT, duration_ms INTEGER, error TEXT, parent_id TEXT, agent_id TEXT,
  tool_use_id TEXT, harness TEXT, decision TEXT, latency_ms REAL
);
CREATE INDEX IF NOT EXISTS ix_events_session ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_tooluse ON events(tool_use_id);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, event_id TEXT, policy TEXT, version INTEGER, mode TEXT, decision TEXT, reason TEXT,
  authority TEXT, reviewer_verdict TEXT, latency_ms REAL, enforced INTEGER DEFAULT 0, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisions_event ON decisions(event_id);
CREATE INDEX IF NOT EXISTS ix_decisions_policy ON decisions(policy);
CREATE TABLE IF NOT EXISTS model_calls (
  id TEXT PRIMARY KEY, session_id TEXT, ts TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
  cache_read INTEGER, cache_write INTEGER DEFAULT 0, cost_usd REAL, latency_ms REAL, context_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS ix_model_calls_session ON model_calls(session_id, ts);
CREATE TABLE IF NOT EXISTS transcript_offsets (session_id TEXT PRIMARY KEY, path TEXT, offset INTEGER);
CREATE TABLE IF NOT EXISTS policies (
  name TEXT PRIMARY KEY, source TEXT, version INTEGER, enabled INTEGER, mode TEXT, authority TEXT,
  params_json TEXT, code_hash TEXT
);
CREATE TABLE IF NOT EXISTS policy_versions (
  name TEXT, version INTEGER, code TEXT, created_at TEXT, notes TEXT, PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS deployments (
  id TEXT PRIMARY KEY, policy TEXT, version INTEGER, mode TEXT, scope TEXT, created_at TEXT, rolled_back_from INTEGER
);
CREATE TABLE IF NOT EXISTS audits (
  id TEXT PRIMARY KEY, goal TEXT, scope_json TEXT, cadence_days INTEGER, last_run TEXT, next_run TEXT, email TEXT
);
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY, audit_id TEXT, failure_mode TEXT, severity TEXT, evidence_json TEXT, session_ids TEXT,
  count INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS insights (
  id TEXT PRIMARY KEY, source TEXT, finding_id TEXT, alert_id TEXT, status TEXT DEFAULT 'open', owner TEXT,
  comments_json TEXT DEFAULT '[]', title TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, name TEXT, description TEXT, enabled INTEGER DEFAULT 1, trigger_kind TEXT,
  trigger_spec_json TEXT, severity TEXT DEFAULT 'warning', channels_json TEXT DEFAULT '[]',
  check_interval_s INTEGER DEFAULT 60, consecutive_checks INTEGER DEFAULT 1, hits INTEGER DEFAULT 0,
  last_checked TEXT
);
CREATE TABLE IF NOT EXISTS alert_firings (id TEXT PRIMARY KEY, alert_id TEXT, ts TEXT, value REAL, insight_id TEXT);
CREATE TABLE IF NOT EXISTS evals (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, spec TEXT, condition TEXT, version INTEGER, enabled INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS eval_results (
  id TEXT PRIMARY KEY, eval_id TEXT, version INTEGER, session_id TEXT, score REAL, passed INTEGER, metric REAL,
  unit TEXT, reasoning TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, event_id TEXT, channel TEXT, sent_at TEXT, answered_at TEXT, answer TEXT, code TEXT,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS packs (
  publisher TEXT, name TEXT, tag TEXT, digest TEXT, manifest_json TEXT, selection_json TEXT, installed_at TEXT,
  PRIMARY KEY (publisher, name)
);
CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT, sql TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reviewer_cache (key TEXT PRIMARY KEY, verdict_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS saved_queries (id TEXT PRIMARY KEY, name TEXT, sql TEXT, created_at TEXT);
