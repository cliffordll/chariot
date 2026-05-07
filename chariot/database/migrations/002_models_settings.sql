-- Chariot schema v2 · 模型配置 DB 化
-- 0.3.0 起 [[models]] / active 不再读 ~/.chariot/config.toml,改存这两张表。
-- ORM 镜像:`ModelRow` / `SettingRow` 在 chariot/server/database/models.py。
--
-- 不在 SQL 里 seed mock —— seed 用 Python 写(`ModelRepo.seed_if_empty()`),
-- 表空时由 lifespan startup 调一次,避免 SQL hardcode "mock" 字符串两份。

CREATE TABLE models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,                   -- 用户面 ID
    type        TEXT    NOT NULL,                          -- ∈ ModelRegistry.known_types()(repo 校验)
    options     TEXT    NOT NULL,                          -- JSON-serialized dict
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_models_name ON models(name);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

PRAGMA user_version = 2;
