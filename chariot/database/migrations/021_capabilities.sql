-- v21:capabilities 表(B5 wave 3)。
--
-- 设计:
-- - 持久化 self-mod / yolo 等 capability flag(per-DB,跨 surface 共享)
-- - `name` 是主键(已知 capability 名集合是有限的:'enable_self_mod' / 'yolo' 等)
-- - `enabled`:0/1,SQLite 无 BOOL 类型,统一用 int
-- - 启动时种两条默认行:都 disabled,用户必须显式 enable
-- - `--yolo` CLI 全局 flag 是 per-process 临时覆盖,不写 DB(短期 / 沙箱场景)
--
-- 跟 capability gating 的关系:
-- - `ApprovalPolicy.auto_approve` 读 Capabilities.yolo
-- - `self_modify_chariot` rule 读 Capabilities.enable_self_mod

CREATE TABLE capabilities (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO capabilities (name, enabled) VALUES
    ('enable_self_mod', 0),
    ('yolo', 0);

PRAGMA user_version = 21;
