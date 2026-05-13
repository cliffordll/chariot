-- migration v23_tool_expansion.sql
-- 0.8.7: 工具扩展 —— 支持自定义工具

ALTER TABLE tools ADD COLUMN source TEXT DEFAULT 'builtin';
ALTER TABLE tools ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE tools ADD COLUMN custom_type TEXT DEFAULT NULL;

-- 回填已有数据
UPDATE tools SET source = 'builtin' WHERE source IS NULL;

PRAGMA user_version = 23;
