-- ai-proactive-deploy schema v1
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
  stream_id TEXT PRIMARY KEY,
  json TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS open_loops (
  loop_id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL,
  text TEXT NOT NULL,
  evidence TEXT NOT NULL,
  due_at REAL NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',   -- open | notified | done | expired
  notified_at REAL NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_loops_stream ON open_loops(stream_id, status, due_at);

CREATE TABLE IF NOT EXISTS scheduled_items (
  item_id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL,
  title TEXT NOT NULL,
  due_at REAL NOT NULL,
  created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | done | expired
  priority REAL NOT NULL DEFAULT 0.5,
  face_risk REAL NOT NULL DEFAULT 0.2,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sched_stream ON scheduled_items(stream_id, status, due_at);

CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  class TEXT NOT NULL,
  quota_class TEXT NOT NULL,
  score REAL NOT NULL,
  priority REAL NOT NULL,
  face_risk REAL NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,                  -- pending | deferred | sent | shadowed | skipped | failed | blocked
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  deferred_until REAL NOT NULL DEFAULT 0,
  defer_count INTEGER NOT NULL DEFAULT 0,
  manual INTEGER NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_intents_stream ON intents(stream_id, status, created_at);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stream_id TEXT NOT NULL,
  day TEXT NOT NULL,
  class TEXT NOT NULL,
  quota_class TEXT NOT NULL,
  kind TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  bubbles INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,                  -- reserved | sent | failed
  ts REAL NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ledger_day ON ledger(day, class, status);
CREATE INDEX IF NOT EXISTS idx_ledger_intent ON ledger(intent_id);

CREATE TABLE IF NOT EXISTS timing_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stream_id TEXT NOT NULL,
  day TEXT NOT NULL,
  hour TEXT NOT NULL,
  budget_class TEXT NOT NULL,
  ts REAL NOT NULL,
  model_ok INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_timing_day ON timing_calls(day, budget_class);
CREATE INDEX IF NOT EXISTS idx_timing_hour ON timing_calls(day, hour);

CREATE TABLE IF NOT EXISTS control (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at REAL NOT NULL
);
