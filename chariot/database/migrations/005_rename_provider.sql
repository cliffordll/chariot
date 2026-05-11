-- Chariot schema v5 · 把 `models` 表改名 `providers`,字段
-- `messages.model_name` 改名 `provider_name`,`logs.model` 改名 `provider`
-- (跨层缩写统一:0.6.0 abstract 层已经是 BaseProvider,DB 层跟上)
--
-- 命名解释:`models` 表过去是"模型实例配置";0.6.0 起一条 entry 就是一个
-- BaseProvider 配置,叫 `providers` 跟代码层 ProviderRegistry / ProviderRepo /
-- ProviderEntry 一致。`chariot provider add` 跟 ProviderRegistry.known_types()
-- 自然对得上;options 里的 `model` 字段(LLM 模型 id,如 claude-haiku-4-5)
-- 仍然叫 `model` —— 那是 Anthropic SDK 透传字段,不在本次重命名范围。
--
-- ALTER TABLE RENAME 在 SQLite ≥ 3.25 都支持;aiosqlite 依赖的 sqlite3 标准
-- 库版本足够。索引跟着重建。

ALTER TABLE models RENAME TO providers;

ALTER TABLE messages RENAME COLUMN model_name TO provider_name;

ALTER TABLE logs RENAME COLUMN model TO provider;

PRAGMA user_version = 5;
