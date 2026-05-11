-- Chariot schema v13: memory platform refinement
-- Existing installations already have `memories` from v7. Upgrade it in place and add
-- event/link tables for memory management and traceability.

ALTER TABLE memories ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS memory_events (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_events_memory_id ON memory_events(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_type ON memory_events(event_type);
CREATE INDEX IF NOT EXISTS idx_memory_events_created_at ON memory_events(created_at);

CREATE TABLE IF NOT EXISTS memory_links (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    link_value TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_links_memory_id ON memory_links(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_links_type ON memory_links(link_type);
CREATE INDEX IF NOT EXISTS idx_memory_links_value ON memory_links(link_value);
CREATE INDEX IF NOT EXISTS idx_memory_links_created_at ON memory_links(created_at);

PRAGMA user_version = 13;
