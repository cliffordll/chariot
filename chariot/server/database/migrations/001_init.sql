-- Chariot schema v1 · 初始化
-- 和 chariot/server/database/models.py 的 ORM 声明保持字段对齐
-- 执行时机:DB 文件首次创建 / PRAGMA user_version = 0 时由 session.init_db() 调用
--
-- v0 架构:server 自己就是智能体 (agent),没有上游代理概念。只有一张 `logs` 表,
-- 记录每次 agent.handle() 的请求流水(model / status / latency / error)。
-- 没有 upstreams 表。
--
-- `id` 类型:32 字符 UUID4 hex(Python `uuid.uuid4().hex`),由 ORM 在插入时填入。
-- 字符串 id 分布式安全、外部引用更稳、日志里一眼能认出。

CREATE TABLE logs (
    id            TEXT    PRIMARY KEY,                    -- 32 字符 UUID4 hex
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    status        TEXT    NOT NULL,                       -- ok / error / timeout
    error         TEXT,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_logs_created_at ON logs(created_at);

PRAGMA user_version = 1;
