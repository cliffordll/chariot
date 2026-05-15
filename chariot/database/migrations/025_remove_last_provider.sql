-- 025:删除 conversations.last_provider 字段
-- provider 由 agent_profile 绑定自动推导,不再单独存储

BEGIN;

-- SQLite 不支持 DROP COLUMN,重建表
CREATE TABLE conversations_new (
    id TEXT PRIMARY KEY,
    title TEXT,
    agent_profile TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    updated_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO conversations_new (id, title, agent_profile, created_at, updated_at)
SELECT id, title, agent_profile, created_at, updated_at FROM conversations;

DROP TABLE conversations;

ALTER TABLE conversations_new RENAME TO conversations;

COMMIT;
