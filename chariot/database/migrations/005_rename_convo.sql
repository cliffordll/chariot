-- Chariot schema v5 · 把 `conversations` 表改名 `convos`,字段
-- `messages.conversation_id` 改名 `convo_id`(全栈 rename:CLI / repo class /
-- 字段 / DB 表 都统一缩写)
--
-- SQLite 没有原生的 ALTER TABLE RENAME COLUMN(3.25 之前)/ ALTER TABLE
-- RENAME TABLE 限制少,这里用 RENAME 路径(SQLite ≥ 3.25 都支持,aiosqlite
-- 依赖的 sqlite3 标准库版本足够)。索引跟着重建。
--
-- 不动 messages 表数据,只 RENAME COLUMN —— 行 id / seq / role / content /
-- model_name / created_at 完全保留。

ALTER TABLE conversations RENAME TO convos;

DROP INDEX IF EXISTS idx_conversations_updated;
CREATE INDEX idx_convos_updated ON convos(updated_at DESC);

ALTER TABLE messages RENAME COLUMN conversation_id TO convo_id;

DROP INDEX IF EXISTS idx_messages_conv;
CREATE INDEX idx_messages_convo ON messages(convo_id, seq);

PRAGMA user_version = 5;
