-- Chariot schema v8 · 平台基础设施最小落点
-- 只落 Phase 4 的最小物理基础:memory / eval / audit / checkpoints / skills。

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_status ON eval_runs(status);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created_at ON eval_runs(created_at);

CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY,
    suite TEXT DEFAULT NULL,
    name TEXT NOT NULL,
    input_payload TEXT NOT NULL DEFAULT '{}',
    expected TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_eval_cases_suite ON eval_cases(suite);
CREATE INDEX IF NOT EXISTS idx_eval_cases_created_at ON eval_cases(created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    status TEXT DEFAULT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_events_status ON audit_events(status);
CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at);

CREATE TABLE IF NOT EXISTS checkpoint_records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT DEFAULT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_records_name ON checkpoint_records(name);
CREATE INDEX IF NOT EXISTS idx_checkpoint_records_kind ON checkpoint_records(kind);
CREATE INDEX IF NOT EXISTS idx_checkpoint_records_created_at ON checkpoint_records(created_at);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT NULL,
    content TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_enabled ON skills(enabled);
