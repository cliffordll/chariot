-- migration v24_conversation_state.sql
-- 0.8.8: 对话状态持久化 —— 支持 agent_profile + last_provider

-- 1) rename last_model → last_provider(语义修正)
ALTER TABLE conversations RENAME COLUMN last_model TO last_provider;

-- 2) 新增 agent_profile 列
ALTER TABLE conversations ADD COLUMN agent_profile TEXT;

PRAGMA user_version = 24;
