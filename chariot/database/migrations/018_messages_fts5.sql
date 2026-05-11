-- v18:messages 表加 FTS5 全文索引,支持 `chariot conversation search` + memory_recall。
--
-- 设计:
-- - 纯 Python 同步(`ConversationRepo` 在 append/delete 时双写),不用 trigger ——
--   现有 migration runner 按 `;` 切 SQL,trigger BEGIN/END 体里嵌的 `;` 会被错切;
--   且 application-level sync 能从 JSON content 抽 text-only,索引干净
-- - FTS table 用 UNINDEXED 列存 message_id / conversation_id 便于查询直接返 id
-- - tokenizer 用 trigram(SQLite 3.34+ 自带,Python 3.12 stdlib SQLite 必有):
--   对 CJK 无 word boundary 问题,中英文混查都通。unicode61 对 CJK 整段当 1 token,
--   不可用;ICU 要 SQLite 额外 build flag,chariot 用 stdlib SQLite 也跑不通
-- - 回填语义:现存消息的 content 是 JSON 原文,初版直接索引原文(noise 可
--   接受);后续版本若改 application-level text-extract,可以一次性 rebuild

CREATE VIRTUAL TABLE messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    role UNINDEXED,
    content,
    tokenize = 'trigram'
);

INSERT INTO messages_fts (message_id, conversation_id, role, content)
SELECT id, conversation_id, role, content FROM messages;

PRAGMA user_version = 18;
