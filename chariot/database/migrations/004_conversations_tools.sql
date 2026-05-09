-- Chariot schema v4 · Conversation 层 + Tool 层(0.4.0)
-- 加三张表:conversations / messages / tools
-- ORM 镜像:`ConversationRow`(原 `ConversationRow`)/ `MessageRow` / `ToolRow` 在 chariot/database/models.py
--
-- conversations.id:ULID 26 字符(校验在 controller 层做,repo 接受任意非空字符串)
-- messages.role:'user' | 'assistant'(Anthropic 协议原生两种,对齐 Claude Code transcript);
--   tool_use 嵌在 assistant.content blocks,tool_result 嵌在 user.content blocks
-- 不写 FK 约束:cascade delete 由 `ConversationRepo.delete()` 手动做(沿用 chariot 风格,
--   不依赖 PRAGMA foreign_keys,行为不被全局开关影响)
--
-- tools 表:0.4.0 不开放 CRUD,只允许改 enabled / options。4 条 seeded fixture
-- (read_file / list_dir / shell_exec / http_get)由 `ToolRepo.seed_if_empty()` 在 lifespan
-- startup 写入,全部默认 disabled —— 用户必须显式打开,符合"安全优先"。

CREATE TABLE conversations (
    id              TEXT    PRIMARY KEY,                        -- ULID(校验在 controller 层)
    title           TEXT,                                        -- 可选,空则 GUI 从首条 user msg 截取展示
    last_model      TEXT,                                        -- 派生:最后一轮 assistant msg 用的 model entry name
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT    NOT NULL,                          -- 引用 conversations.id(无 FK 约束)
    seq              INTEGER NOT NULL,                          -- 会话内单调 0 起
    role             TEXT    NOT NULL,                          -- 'user' | 'assistant'
    content          TEXT    NOT NULL,                          -- JSON,原样存 anthropic content(字符串或 blocks 数组)
    model_name       TEXT,                                      -- 仅 role='assistant' 行非空,记本轮用的 entry name
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, seq)
);

CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);

CREATE TABLE tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,                        -- 用户面 ID(也是 ToolRegistry 的 type key)
    type        TEXT    NOT NULL,                               -- ∈ ToolRegistry.known_types(),0.4.0 与 name 同形
    enabled     INTEGER NOT NULL DEFAULT 0,                     -- 0/1
    options     TEXT    NOT NULL DEFAULT '{}',                  -- JSON,workdir / allowed_domains / max_bytes 等
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tools_name ON tools(name);

PRAGMA user_version = 4;
