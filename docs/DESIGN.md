# Chariot 架构设计(0.3.1)

> **当前版本**:`0.3.1`(开发中)
> **上一版归档**:[`docs/history/0.2.6/DESIGN.md`](history/0.2.6/DESIGN.md)
>
> **0.3.1 关键变更**(vs 0.3.0):
> - **路由模型重构**:`active` 概念删除。client 必须在 `body.model` 写 chariot 的
>   entry name,Agent 按 name 路由到对应 Model 实例,server 不再持有"当前选哪个"
>   的状态
> - **`POST /admin/models` 切 active 端点删除** + `chariot model use` CLI 子命令删除
> - **`models` 表加 `params` 列**(JSON):runtime sampling 默认值(temperature /
>   top_p / max_tokens 等),前端发请求时从 entry.params 取并写进 body;
>   server 不主动注入 body。Chat 页"高级参数面板"删除 —— sampling 配置归属
>   Models 页(行展开里 ParamsEditor)单一入口
> - **`settings` 表 drop**:0.3.0 加进来主要存 active_model,0.3.1 active 退役后
>   该表也用不上
> - **Models 页行内拆开展示** model / api_key(脱敏) / base_url 主键 + 行展开
>   `ParamsEditor` 编辑 params
>
> **0.3.0 关键变更**(vs 0.2.6,沿用):
> - 模型配置从 `~/.chariot/config.toml` 迁到 chariot 内置 SQLite(`models` 表)
> - Models 页 / CLI / admin API 上 add/edit/rm/duplicate
> - `chariot config init/show` 子命令组废弃
> - 不做老用户 TOML 迁移

---

## 1. 一句话总结

**Chariot = 本机 HTTP server + Anthropic Messages 协议 + 按 entry-name 路由的 Model 网关**

- 对外只接 `POST /v1/messages`(Anthropic 原生)
- 内部 `Model` 实现按 DB 里的 entries 注入(默认 seed `mock`,真实模型如 `AnthropicModel`)
- client 在 `body.model` 写 chariot 的 entry name,Agent 按 name dispatch;
  缺失 / 未知 → 400 `unknown_model_name`
- OpenAI 客户端通过外部转换器(LiteLLM / claude-code-router 等)接入

---

## 2. 分层

```
客户端 (Anthropic SDK / claude code / 自定义)
        │  POST /v1/messages   body.model = <entry-name>
        ▼
Controller (controller/dataplane.py) — 仅读 body,转给 Agent
        │
        ▼
Agent (server/agent.py) 类级单例
   持有 _models: dict[str, Model]
        │  按 body.model lookup
        ▼
Model Protocol (server/model/base.py)
   └── MockModel / AnthropicModel / ...

Lifespan startup:
   init_db() → migrations(含 v3:加 models.params 列 + drop settings)
              ↓
   ModelRepo.seed_if_empty(s)         # 表空 → seed mock entry
   ModelRepo.list_entries(s) → ChariotConfig
              ↓
   Agent.install_from_config(config)  # eager build name → Model 字典
```

`Agent` 与 0.2.x 的本质区别:从"持有单一 active model"变成"持有 name → Model
字典",请求路由由 `body.model` 决定。

---

## 3. 唯一对外端点(沿用 0.2.6,见归档 §3)

`POST /v1/messages`,Anthropic Messages 协议。SSE 事件序列不变。

**唯一新约束**:body 必须含 `model` 字段且值是 chariot 已知 entry name(`chariot
model list` 或 `GET /admin/models` 看可用)。缺失 / 空串 / 未知 → 400 ServiceError
`unknown_model_name`(沿用 0.2.x 的 `chariot_error` JSON 形态)。

---

## 4-5. 数据面流程 + Model 接口契约(沿用 0.2.6,见归档 §4 / §5)

接口契约硬约束:

1. **无状态**;**不碰 DB**;**不感知"上游"**
2. `name: str` 属性 + `async respond(body, *, stream) -> Response` 方法
3. 注册到 ModelRegistry 用 `@register("type")` 装饰 + 实现 `from_config(options)`
   classmethod

`AnthropicModel` 仍按 `options.model` 改写 body 里的 model 字段(0.3.0 已把 key
从 `model_id` 重命名为 `model`)再转上游。chariot 这一层"客户端写 entry name →
server 改写 body.model 为上游 model id"是协议透传的核心动作。

---

## 6. 模型管理层(0.3.1 路由重构)

### 6.1 数据存储

```sql
CREATE TABLE models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,
    options     TEXT NOT NULL,                   -- build Model 实例所需(JSON dict)
    params      TEXT NOT NULL DEFAULT '{}',      -- runtime sampling 默认值(JSON dict)
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_models_name ON models(name);
```

- `name` 唯一(用户面 ID,client 在 `body.model` 写这个)
- `type` ∈ `ModelRegistry.known_types()`;repo 层 insert/update 时校验
- `options`:**build Model 实例所需**(api_key / model / base_url ...)
- `params`:**runtime 调用时的默认 sampling**(temperature / top_p / max_tokens ...)
  —— 服务端只读暴露给前端,**不主动注入 body**;Chat 页发请求时从当前 entry 的
  params 现取写进 body。**Chat 页无 sampling UI**(0.3.1 删 AdvancedParamsPanel),
  改默认值统一去 Models 页编辑该 entry 的 params

`settings` 表 0.3.0 加进来,0.3.1 drop —— active 概念退役后该表无用途;未来如有
全局 KV 配置需求再加回。

### 6.2 类设计

| 类 | 文件 | 职责 |
|---|---|---|
| `ModelRow` ORM | `chariot/server/database/models.py` | sqlalchemy 映射 models 表(含 params 列) |
| `ModelRepo` | `chariot/server/repository/model_repo.py` | list / get / create / update / delete / duplicate;参数校验、JSON 序列化、`seed_if_empty()`(只 seed entry,不写 active) |
| `ModelEntry` | `chariot/server/config.py` | 数据形态 = name / type / options / params(0.3.1 加 params 字段) |
| `ChariotConfig` | 同上 | **纯 entries 容器**(0.3.1 删 active);classmethod `from_db(session)` 唯一装载入口 |
| `Agent` | `chariot/server/agent.py` | 类级单例;`models: dict[str, Model]`;`install_from_config / refresh_config / handle` |
| `ModelRegistry` | `chariot/server/model/registry.py` | 0.2.x 沿用,无改动 |

### 6.3 启动流程(seed mock)

```python
async with lifespan(_app):
    await init_db()                         # 含 v3 migration
    async with session_maker() as s:
        await ModelRepo(s).seed_if_empty()  # 表空 → seed ('mock', 'mock', '{}', '{}')
        config = await ChariotConfig.from_db(s)
    Agent.install_from_config(config)       # eager build name → Model 字典
    yield
    Agent.uninstall()
    await dispose_db()
```

`Agent.install_from_config`:

```python
@classmethod
def install_from_config(cls, config: ChariotConfig) -> Agent:
    agent = cls()
    for entry in config.models:
        agent.models[entry.name] = ModelRegistry.build(entry)
    cls._current = agent
    cls._config = config
    return agent
```

### 6.4 CRUD 后的 Agent 同步

`POST /admin/models/entries` 等写操作完成后,controller 层负责:

1. `ModelRepo.create / update / delete / duplicate` 持久化
2. 重新装载 `ChariotConfig.from_db(session)`(纯读)
3. `Agent.refresh_config(config)` —— **全量 rebuild** Model 字典(简化:删除的
   entry 自动从字典移除,改 options 的 entry 自动重 build)

简化策略权衡:全量 rebuild 比"diff + 选择性 rebuild"代码少一半,代价是改一条
entry 时也会重 build 其它 entry 的 Model 实例。每个 Model 实例就是 `httpx.AsyncClient`
+ 几个字段,build 成本可忽略;在 entry 数远小于 100 的现实场景下不是问题。

### 6.5 不做老用户迁移

0.3.0 已经决定不读旧 `~/.chariot/config.toml`,0.3.1 也不动这条 —— 用户在
Models 页加 entries 即可。0.3.0 升 0.3.1 的破坏性影响:

- 旧 `body.model = "claude-opus-4-5"`(写上游 model id)→ 400(chariot 不知道这
  个 entry name)。需要改成 `body.model = <chariot 的 entry name>`,Models 页加 entry 时
  设的那个 name
- 现实迁移:大多数集成层(LiteLLM / OpenAI Anthropic 适配器 / Chat 页 / chariot
  CLI)在客户端有"model name override",改一处即可

---

## 7. 表清单

```sql
-- 0.1.0
CREATE TABLE logs ( ... );
CREATE INDEX idx_logs_created_at ON logs(created_at);

-- 0.3.0 新增,0.3.1 演进
CREATE TABLE models (
    ...,
    options TEXT NOT NULL,
    params  TEXT NOT NULL DEFAULT '{}'   -- 0.3.1 加
);

-- 0.3.0 新增,0.3.1 drop
-- CREATE TABLE settings (...);   ← 已退役
```

migrations 顺序:`v0`(空)→ `v1`(logs)→ `v2`(0.3.0:加 models / settings)→
`v3`(0.3.1:`ALTER models ADD COLUMN params` + `DROP TABLE settings`)。
`PRAGMA user_version` 沿用 0.1.0 的迁移机制。

---

## 8. 管理面

```
GET  /admin/ping       → {ok: true}
GET  /admin/status     → {version, uptime_ms, entries_count, url}    # 0.3.1:删 model 字段
POST /admin/shutdown   → graceful shutdown
GET  /admin/logs       → list LogOut(limit / offset / since)
GET  /admin/stats      → {period, total_requests, success_rate, avg_latency_ms}

GET  /admin/models                       → {available, types, entries: [{name, type, options, params}]}
POST /admin/models/{name}/probe          → 探针(0.2.5 起,沿用)

POST   /admin/models/entries             → create entry(body: {name, type, options, params?})
PUT    /admin/models/entries/{name}      → update entry(body: {type?, options?, params?})
DELETE /admin/models/entries/{name}      → delete entry(0.3.1:任意 entry 都能删)
POST   /admin/models/entries/{name}/duplicate → 复制(body: {as: <new-name>})
```

**0.3.1 删除的端点**:`POST /admin/models {name}`(切 active)。

CRUD 行为细节:

- **create**:校验 name 唯一、type ∈ known_types、options/params 可 JSON 序列化;
  重名 → 409 `name_exists`;type 未注册 → 400 `unknown_type`
- **update**:可改 type / options / params;改完 server 立刻 rebuild 该 entry 的
  Model 实例(新 options 即时生效)
- **delete**:0.3.1 起任意 entry 都能删(active 概念退役,不再有 cannot_delete_active)
- **duplicate**:body.as 缺省 `<name>_copy`,碰撞自动加序号(`<name>_copy_2 / _3`)。
  options 和 params 都从源复制

---

## 9. 单例 / lifespan(见 §6.3)

---

## 10. AnthropicModel 实现要点(沿用 0.2.6,见归档 §10)

API key 来源、错误映射、流式中途断开行为均不变。`options` key 0.3.0 起从
`model_id` 改名为 `model`(对齐 Anthropic 协议原词)。

---

## 11-12. 设计模式 + 测试覆盖

### 设计模式新增(0.3.x 沿用)

| 模式 | 用在哪 | 解决什么 |
|---|---|---|
| **Repository** | `ModelRepo` 在 `repository/` 层 | 隔离持久化细节;Agent / Controller 不直接碰 ORM |
| **Routing dispatch** | `Agent.handle` 按 body.model 查字典 | 多 Model 共存的最小路由 |

其它(Strategy / Registry / Singleton)沿用,见归档 §11。

### 测试覆盖

| 文件 | 覆盖 |
|---|---|
| `tests/server/test_model_repo.py` | CRUD + 校验 + duplicate 命名规则 + seed_if_empty + params |
| `tests/server/test_config.py` | ChariotConfig 数据形态 + from_db |
| `tests/server/test_admin_models.py` | entries CRUD 端点 + probe + 0.3.1 删 active 行为 |
| `tests/server/test_agent.py` | install_from_config / refresh_config / handle 路由 + unknown_model_name |
| `tests/server/test_dataplane.py` | /v1/messages 按 body.model 路由 + 缺失/未知 → 400 |

---

## 13. 0.4.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.4.0**:Agent 层多轮记忆 + 工具调用
- **0.5.0**:进化循环
- **0.6.0**:多 Agent 实例
