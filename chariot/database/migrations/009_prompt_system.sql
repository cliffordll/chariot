-- Chariot schema v9: prompt system minimal foundation
--
-- 目标:
-- - prompt_bundles: prompt 组合单元定义
-- - prompt_versions: bundle 的版本快照
-- - prompt_traces: turn 级 prompt 追踪
--
-- 说明:
-- - 这里先只落最小 schema，不做前后兼容迁移
CREATE TABLE prompt_bundles (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT,
    layers        TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE prompt_versions (
    id            TEXT PRIMARY KEY,
    bundle_id     TEXT NOT NULL,
    version       TEXT NOT NULL,
    spec          TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bundle_id, version)
);

CREATE TABLE prompt_traces (
    id              TEXT PRIMARY KEY,
    bundle_id       TEXT NOT NULL,
    version_id      TEXT NOT NULL,
    conversation_id TEXT,
    provider_name   TEXT NOT NULL,
    model           TEXT,
    request         TEXT NOT NULL DEFAULT '{}',
    source_refs     TEXT NOT NULL DEFAULT '[]',
    prompt_size     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_prompt_versions_bundle_id ON prompt_versions(bundle_id);
CREATE INDEX idx_prompt_traces_bundle_id ON prompt_traces(bundle_id, created_at DESC);
CREATE INDEX idx_prompt_traces_conversation_id ON prompt_traces(conversation_id, created_at DESC);

PRAGMA user_version = 9;
