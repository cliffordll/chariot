-- Chariot schema v11: context snapshots and traces

CREATE TABLE context_snapshots (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    provider_name TEXT NOT NULL,
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
    provider_name TEXT NOT NULL,
    model TEXT,
    prompt_trace_id TEXT,
    policy TEXT NOT NULL DEFAULT '{}',
    selected_refs TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_context_snapshots_conversation_id ON context_snapshots(conversation_id);
CREATE INDEX idx_context_snapshots_created_at ON context_snapshots(created_at);
CREATE INDEX idx_context_traces_snapshot_id ON context_traces(snapshot_id);
CREATE INDEX idx_context_traces_conversation_id ON context_traces(conversation_id);
CREATE INDEX idx_context_traces_created_at ON context_traces(created_at);

PRAGMA user_version = 11;
