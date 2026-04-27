-- Chariot schema v3 · 路由模型重构(0.3.1)
-- 改动:
-- 1. models 表加 params 列(JSON,默认 '{}'):runtime sampling 默认值,跟 options 解耦
-- 2. drop settings 表:active 概念删除,client 用 body.model 写 entry name 路由
--
-- 老用户(user_version=2)升级:旧 settings.active_model 会丢,client 必须改成
-- 显式在 body.model 写 entry name(README 已说明)。

ALTER TABLE models ADD COLUMN params TEXT NOT NULL DEFAULT '{}';

DROP TABLE settings;

PRAGMA user_version = 3;
