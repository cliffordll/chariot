-- Chariot schema v9 · `messages.id` 升到文本主键
--
-- 目标:
-- - fresh DB:最终 `messages.id` 是 TEXT 主键;ORM 新插入消息用 ULID
-- - existing DB:保留历史消息,把旧 int id 转成稳定 legacy 文本 id
--
-- 说明:
-- - SQLite 纯 SQL migration 不便直接生成真 ULID,所以历史行使用
--   `msg_legacy_<旧 int>` 文本格式;迁移后的新写入行走 ORM default=_new_ulid。

CREATE TABLE messages_v9 (
    id              TEXT    PRIMARY KEY,
    convo_id        TEXT    NOT NULL,
    seq             INTEGER NOT NULL,
    role            TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    provider_name   TEXT,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(convo_id, seq)
);

INSERT INTO messages_v9 (id, convo_id, seq, role, content, provider_name, created_at)
SELECT
    'msg_legacy_' || CAST(id AS TEXT),
    convo_id,
    seq,
    role,
    content,
    provider_name,
    created_at
FROM messages;

DROP TABLE messages;

ALTER TABLE messages_v9 RENAME TO messages;

CREATE INDEX idx_messages_convo ON messages(convo_id, seq);

PRAGMA user_version = 9;
