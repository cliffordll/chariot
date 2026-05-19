CREATE TABLE context_bundles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE context_versions (
    id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    version TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bundle_id, version)
);

CREATE INDEX idx_context_bundles_name ON context_bundles(name);
CREATE INDEX idx_context_versions_bundle_id ON context_versions(bundle_id);

ALTER TABLE context_traces ADD COLUMN bundle_id TEXT;
ALTER TABLE context_traces ADD COLUMN version_id TEXT;
CREATE INDEX idx_context_traces_bundle_id ON context_traces(bundle_id);
CREATE INDEX idx_context_traces_version_id ON context_traces(version_id);

ALTER TABLE agent_profiles ADD COLUMN context_id TEXT;
CREATE INDEX idx_agent_profiles_context_id ON agent_profiles(context_id);

PRAGMA user_version = 7;
