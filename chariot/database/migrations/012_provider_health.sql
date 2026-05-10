-- Chariot schema v12: provider health snapshots

CREATE TABLE provider_health (
    provider_name TEXT PRIMARY KEY,
    last_ok INTEGER NOT NULL DEFAULT 1,
    latency_ms INTEGER,
    error_code TEXT,
    error_message TEXT,
    last_probe_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_provider_health_last_probe_at ON provider_health(last_probe_at);

PRAGMA user_version = 12;
