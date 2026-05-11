-- Chariot schema v16: toolsets (0.7.2-tool)
--
-- 引入命名 toolset 实体,作为 agent_profile.tool_profile 字段的引用目标。
-- 设计文档:docs/tool-profile-design.md。
--
-- 语义:
-- - toolset 是 "命名一组 tool name 的集合";agent_profile.tool_profile = toolset.name
-- - agent_profile.tool_profile 是 "软引用",dangling reference 不阻断 task
--   执行(AIAgent 走 fallback)。本 migration 不加 FK 约束
-- - toolset_members.toolset_name → toolsets.name ON DELETE CASCADE,删 toolset
--   时成员自动清理
-- - toolset_members 不引用 tools.name(tool 名是动态的、可能未来注册或卸载)

CREATE TABLE IF NOT EXISTS toolsets (
    name TEXT PRIMARY KEY,
    description TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS toolset_members (
    toolset_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (toolset_name, tool_name),
    FOREIGN KEY (toolset_name) REFERENCES toolsets(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_toolset_members_tool_name ON toolset_members(tool_name);

PRAGMA user_version = 16;
