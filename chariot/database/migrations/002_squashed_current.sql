-- Chariot schema v2 · squash 0.1.0 -> current
-- 保留 001_init.sql 作为 v0.1.0 历史基线;本文件负责把仅含 logs 表的 v1 库
-- 一次性升级到当前最终 schema。

ALTER TABLE logs RENAME TO logs_legacy;

CREATE TABLE logs (
    id TEXT PRIMARY KEY,
    provider TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO logs (id, provider, input_tokens, output_tokens, latency_ms, status, error, created_at)
SELECT id, model, input_tokens, output_tokens, latency_ms, status, error, created_at
FROM logs_legacy;

DROP TABLE logs_legacy;

CREATE INDEX idx_logs_created_at ON logs(created_at);

CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    options TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_providers_slug ON providers(slug);
CREATE INDEX idx_providers_name ON providers(name);

INSERT INTO providers (id, slug, name, type, options, params, created_at, updated_at)
VALUES ('provider_mock', 'mock', 'Mock', 'mock', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    default_provider_id TEXT DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO settings (id, default_provider_id, created_at, updated_at)
VALUES (1, 'provider_mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    agent_profile TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider_name TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, seq)
);

CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);

CREATE TABLE tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'builtin',
    description TEXT NOT NULL DEFAULT '',
    custom_type TEXT DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tools_name ON tools(name);

CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_memories_kind ON memories(kind);
CREATE INDEX idx_memories_created_at ON memories(created_at);

CREATE TABLE memory_events (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_memory_events_memory_id ON memory_events(memory_id);
CREATE INDEX idx_memory_events_type ON memory_events(event_type);
CREATE INDEX idx_memory_events_created_at ON memory_events(created_at);

CREATE TABLE memory_links (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    link_value TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_memory_links_memory_id ON memory_links(memory_id);
CREATE INDEX idx_memory_links_type ON memory_links(link_type);
CREATE INDEX idx_memory_links_value ON memory_links(link_value);
CREATE INDEX idx_memory_links_created_at ON memory_links(created_at);

CREATE TABLE eval_runs (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_eval_runs_status ON eval_runs(status);
CREATE INDEX idx_eval_runs_created_at ON eval_runs(created_at);

CREATE TABLE eval_cases (
    id TEXT PRIMARY KEY,
    suite TEXT DEFAULT NULL,
    name TEXT NOT NULL,
    input_payload TEXT NOT NULL DEFAULT '{}',
    expected TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_eval_cases_suite ON eval_cases(suite);
CREATE INDEX idx_eval_cases_created_at ON eval_cases(created_at);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    status TEXT DEFAULT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_events_type ON audit_events(event_type);
CREATE INDEX idx_audit_events_status ON audit_events(status);
CREATE INDEX idx_audit_events_created_at ON audit_events(created_at);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT DEFAULT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_checkpoints_name ON checkpoints(name);
CREATE INDEX idx_checkpoints_kind ON checkpoints(kind);
CREATE INDEX idx_checkpoints_created_at ON checkpoints(created_at);

CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT NULL,
    content TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_skills_name ON skills(name);
CREATE INDEX idx_skills_enabled ON skills(enabled);

CREATE TABLE prompt_bundles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    layers TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE prompt_versions (
    id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    version TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bundle_id, version)
);

CREATE TABLE prompt_traces (
    id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    conversation_id TEXT,
    provider_id TEXT,
    provider_name_snapshot TEXT NOT NULL,
    model TEXT,
    request TEXT NOT NULL DEFAULT '{}',
    source_refs TEXT NOT NULL DEFAULT '[]',
    prompt_size INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_prompt_versions_bundle_id ON prompt_versions(bundle_id);
CREATE INDEX idx_prompt_traces_bundle_id ON prompt_traces(bundle_id, created_at DESC);
CREATE INDEX idx_prompt_traces_conversation_id ON prompt_traces(conversation_id, created_at DESC);
CREATE INDEX idx_prompt_traces_provider_id ON prompt_traces(provider_id);

CREATE TABLE context_snapshots (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    provider_id TEXT,
    provider_name_snapshot TEXT NOT NULL,
    model TEXT,
    request TEXT NOT NULL DEFAULT '{}',
    slices TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    context_size INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE context_traces (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    conversation_id TEXT,
    provider_id TEXT,
    provider_name_snapshot TEXT NOT NULL,
    model TEXT,
    prompt_trace_id TEXT,
    policy TEXT NOT NULL DEFAULT '{}',
    selected_refs TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_context_snapshots_conversation_id ON context_snapshots(conversation_id);
CREATE INDEX idx_context_snapshots_created_at ON context_snapshots(created_at);
CREATE INDEX idx_context_snapshots_provider_id ON context_snapshots(provider_id);
CREATE INDEX idx_context_traces_snapshot_id ON context_traces(snapshot_id);
CREATE INDEX idx_context_traces_conversation_id ON context_traces(conversation_id);
CREATE INDEX idx_context_traces_created_at ON context_traces(created_at);
CREATE INDEX idx_context_traces_provider_id ON context_traces(provider_id);

CREATE TABLE provider_health (
    provider_id TEXT PRIMARY KEY,
    provider_name_snapshot TEXT NOT NULL,
    last_ok INTEGER NOT NULL DEFAULT 1,
    latency_ms INTEGER DEFAULT NULL,
    error_code TEXT DEFAULT NULL,
    error_message TEXT DEFAULT NULL,
    last_probe_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_provider_health_last_probe_at ON provider_health(last_probe_at);

CREATE TABLE agent_profiles (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    prompt_id TEXT,
    tool_profile TEXT,
    provider_id TEXT,
    budget TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    reflection_enabled INTEGER NOT NULL DEFAULT 0,
    reflection_max_retries INTEGER NOT NULL DEFAULT 2,
    default_skill TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_agent_profiles_role ON agent_profiles(role);
CREATE INDEX idx_agent_profiles_prompt_id ON agent_profiles(prompt_id);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_profile TEXT,
    parent_task_id TEXT,
    owner TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    artifacts TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_kind ON tasks(kind);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_agent_profile ON tasks(agent_profile);
CREATE INDEX idx_tasks_parent_task_id ON tasks(parent_task_id);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);

CREATE TABLE task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'manual',
    resume_from_run_id TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX idx_task_runs_task_id ON task_runs(task_id);
CREATE INDEX idx_task_runs_status ON task_runs(status);
CREATE INDEX idx_task_runs_resume_from ON task_runs(resume_from_run_id);
CREATE INDEX idx_task_runs_started_at ON task_runs(started_at);

CREATE TABLE scheduled_jobs (
    name TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    cron TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    agent_profile TEXT,
    last_run_status TEXT,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_scheduled_jobs_enabled ON scheduled_jobs(enabled);
CREATE INDEX idx_scheduled_jobs_agent_profile ON scheduled_jobs(agent_profile);

CREATE TABLE job_runs (
    id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    task_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX idx_job_runs_job_name ON job_runs(job_name);
CREATE INDEX idx_job_runs_task_id ON job_runs(task_id);
CREATE INDEX idx_job_runs_status ON job_runs(status);
CREATE INDEX idx_job_runs_started_at ON job_runs(started_at);

CREATE TABLE toolsets (
    name TEXT PRIMARY KEY,
    description TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE toolset_members (
    toolset_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (toolset_name, tool_name)
);

CREATE INDEX idx_toolset_members_tool_name ON toolset_members(tool_name);

CREATE TABLE trace_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    agent_profile TEXT,
    task_id TEXT,
    task_run_id TEXT,
    provider_id TEXT,
    provider_name_snapshot TEXT NOT NULL,
    model TEXT,
    prompt_trace_id TEXT,
    context_trace_id TEXT,
    status TEXT NOT NULL,
    stop_reason TEXT,
    error_type TEXT,
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd REAL,
    cost_status TEXT,
    duration_ms INTEGER,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE trace_provider_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    provider_id TEXT,
    provider_name_snapshot TEXT NOT NULL,
    model TEXT,
    log_id TEXT,
    request_summary TEXT NOT NULL DEFAULT '{}',
    response_summary TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    latency_ms INTEGER,
    error_type TEXT
);

CREATE TABLE trace_tool_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    provider_call_id TEXT,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL DEFAULT '{}',
    result_summary TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error_message TEXT,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP
);

CREATE TABLE trace_checkpoints (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    snapshot_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trace_turns_conversation ON trace_turns(conversation_id);
CREATE INDEX idx_trace_turns_task ON trace_turns(task_id);
CREATE INDEX idx_trace_turns_status ON trace_turns(status);
CREATE INDEX idx_trace_turns_started ON trace_turns(started_at DESC);
CREATE INDEX idx_trace_turns_provider_id ON trace_turns(provider_id);
CREATE INDEX idx_trace_provider_calls_turn ON trace_provider_calls(turn_id);
CREATE INDEX idx_trace_provider_calls_provider_id ON trace_provider_calls(provider_id);
CREATE INDEX idx_trace_tool_calls_turn ON trace_tool_calls(turn_id);
CREATE INDEX idx_trace_tool_calls_tool ON trace_tool_calls(tool_name);
CREATE INDEX idx_trace_checkpoints_turn ON trace_checkpoints(turn_id);

CREATE TABLE auxiliary_clients (
    name TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO auxiliary_clients (name, provider_id, model, params, created_at, updated_at)
VALUES ('summarizer', 'provider_mock', NULL, '{"max_tokens": 512}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

CREATE TABLE capabilities (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO capabilities (name, enabled) VALUES
    ('enable_self_mod', 0),
    ('yolo', 0);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    role UNINDEXED,
    content,
    tokenize = 'trigram'
);

INSERT INTO messages_fts (message_id, conversation_id, role, content)
SELECT id, conversation_id, role, content FROM messages;

PRAGMA user_version = 2;
