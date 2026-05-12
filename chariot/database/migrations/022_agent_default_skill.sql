-- v22:agent_profiles 加 default_skill 字段(B6 wave 2)。
--
-- 设计:
-- - 让 agent_profile 可以预绑一个 skill,chat 不显式 --skill 时也走该 skill
-- - dangling reference(skill name 不存在 / disabled)→ 走 fallback,不阻断
--   ——跟 prompt_bundle / tool_profile 同款 fallback 语义
-- - NULL = 未绑定(常态;走全局,不注入 skill 块)

ALTER TABLE agent_profiles ADD COLUMN default_skill TEXT;

PRAGMA user_version = 22;
