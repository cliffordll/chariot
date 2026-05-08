-- Chariot schema v7 · 给 `providers` 表加 `is_default` 列(默认 provider 机制)。
--
-- 同 commit 落 `ProviderRepo.get_default / set_default / unset_default` +
-- CLI `provider use` / `provider show` 命令。`chariot chat` 不传 `--provider`
-- 时走 is_default=1 那行。
--
-- 约束:同时至多一行为 1(由 `set_default` 原子保证:清所有 + 置选中)。
-- 列只增不减:NOT NULL DEFAULT 0,跟现有行兼容(全部 0,等 set_default 触发)。

ALTER TABLE providers ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 7;
