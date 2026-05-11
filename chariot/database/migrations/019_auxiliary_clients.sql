-- v19:auxiliary_clients 表 —— Context auto-compression 副 model 路由
--
-- 设计:
-- - "副 model" 不是新增 BaseProvider 子类,而是"指向某个现有 providers 表 entry,
--   但带独立 model id / params"的 wrapper(避免摘要本身耗光主任务的 token 预算)
-- - `name` 作主键,业务面 ID(如 'summarizer' / 'critic_aux')
-- - `provider_entry` 引用 providers.name(运行时校验存在,DB 不加 FK 兼容 SQLite)
-- - `model` 可空;空时用 provider_entry 的 default model(provider options.model)
-- - `params` JSON dict:max_tokens / temperature / 等独立 sampling
-- - 0.8.2 B3 wave 2:默认 seed 一条 'summarizer' 指向 mock,开箱即用

CREATE TABLE auxiliary_clients (
    name TEXT PRIMARY KEY,
    provider_entry TEXT NOT NULL,
    model TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO auxiliary_clients (name, provider_entry, model, params)
VALUES ('summarizer', 'mock', NULL, '{"max_tokens": 512}');

PRAGMA user_version = 19;
