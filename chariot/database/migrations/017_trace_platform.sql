-- Chariot schema v17: trace platform(Phase B1)
--
-- 把"一次 turn 发生了什么"从 logs / audit_events / prompt_traces / context_traces
-- 散表统一成 trace 树:trace_turns(根) -> trace_provider_calls + trace_tool_calls +
-- trace_checkpoints。后续 reflection / evaluation / skill curator / RL trajectory
-- 全部读这套数据。
--
-- 设计文档:docs/evolution-design.md §4。
--
-- 数据粒度(决策 1):**只存摘要**。完整 request / response payload 仍在 logs 表
-- (trace_provider_calls.log_id 关联);trace 层只摘要(message_count / tool_count /
-- stop_reason / usage / 错误码)。隐私 / 大小都可控。

CREATE TABLE IF NOT EXISTS trace_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    agent_profile TEXT,
    task_id TEXT,
    task_run_id TEXT,
    provider_name TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS trace_provider_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model TEXT,
    log_id TEXT,
    request_summary TEXT NOT NULL DEFAULT '{}',
    response_summary TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    latency_ms INTEGER,
    error_type TEXT,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_tool_calls (
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
    finished_at TIMESTAMP,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_checkpoints (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    snapshot_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_turns_conversation ON trace_turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_trace_turns_task ON trace_turns(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_turns_status ON trace_turns(status);
CREATE INDEX IF NOT EXISTS idx_trace_turns_started ON trace_turns(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_provider_calls_turn ON trace_provider_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_trace_tool_calls_turn ON trace_tool_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_trace_tool_calls_tool ON trace_tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_trace_checkpoints_turn ON trace_checkpoints(turn_id);

PRAGMA user_version = 17;
