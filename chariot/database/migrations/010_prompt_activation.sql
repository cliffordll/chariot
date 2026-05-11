-- Chariot schema v10: prompt activation flags

ALTER TABLE prompt_bundles ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prompt_versions ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0;

UPDATE prompt_bundles
SET is_active = 1
WHERE name = 'default';

UPDATE prompt_versions
SET is_active = 1
WHERE bundle_id = (
    SELECT id FROM prompt_bundles WHERE name = 'default' LIMIT 1
) AND version = 'v1';

PRAGMA user_version = 10;
