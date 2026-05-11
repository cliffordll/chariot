-- v20:agent_profiles 加 reflection 字段(B4 wave 3)。
--
-- 设计:
-- - reflection 默认关:即便 AIAgent 装了 critic(`auxiliary_clients.name='critic'`),
--   也只有显式打开 `reflection_enabled=1` 的 agent_profile 才会触发 reflect-then-retry
-- - max_retries 默认 2:初次跑 + 至多 2 次 retry,跟 B4 wave 2 ChatRequest 默认值一致
-- - 现存行 backfill:DEFAULT 0 / 2 满足

ALTER TABLE agent_profiles ADD COLUMN reflection_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_profiles ADD COLUMN reflection_max_retries INTEGER NOT NULL DEFAULT 2;

PRAGMA user_version = 20;
