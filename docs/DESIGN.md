# Chariot 架构设计(0.3.0)

> **当前版本**:`0.3.0`(开发中)
> **上一版归档**:[`docs/history/0.2.6/DESIGN.md`](history/0.2.6/DESIGN.md)
>
> **0.3.0 关键变更**(vs 0.2.6):
> - **模型配置 DB 化**:`~/.chariot/config.toml` 不再是真源,所有 `[[models]]` / active
>   选择改存 chariot 自己的 SQLite(新增 `models` / `settings` 表)
> - **Models 页 CRUD**:在 UI / CLI / admin API 上 add / edit / rm / duplicate model
>   entries,所有改动持久化到 DB 并即时 reload Agent
> - **`chariot config init/show` CLI 子命令组废弃**(配置文件这个概念去掉)
> - **不做老用户迁移**:旧 `~/.chariot/config.toml` 不再被读取,用户在 Models
>   页重新加 entries(决策来自 0.3.0 起步会话)

---

## 1. 一句话总结

**Chariot = 本机 HTTP server + Anthropic Messages 协议 + 可热插拔 Model**

- 对外只接 `POST /v1/messages`(Anthropic 原生)
- 内部 `Model` 实现按 DB 里的 entries 注入(默认 seed `mock`,真实模型如 `AnthropicModel`)
- OpenAI 客户端通过外部转换器(LiteLLM / claude-code-router 等)接入

---

## 2. 分层(同 0.2.6,引用 `history/0.2.6/DESIGN.md` §2)

```
客户端 (Anthropic SDK / claude code / 自定义)
        │  POST /v1/messages
        ▼
Controller (controller/dataplane.py)
        │
        ▼
Agent (server/agent.py) 类级单例
        │
        ▼
Model Protocol (server/model/base.py)
   └── MockModel / AnthropicModel / ...

Lifespan startup:
   init_db() → migrations(含 v2:建 models / settings + seed mock)
              ↓
   ModelRepo.list_entries() / get_active() → ChariotConfig
              ↓
   Agent.install_from_db(config) → ModelRegistry.build(active_entry)
```

---

## 3. 唯一对外端点(沿用 0.2.6,见归档 §3)

`POST /v1/messages`,Anthropic Messages 协议。SSE 事件序列不变。

---

## 4-5. 数据面流程 + Model 接口契约(沿用 0.2.6,见归档 §4 / §5)

接口契约硬约束:

1. **无状态**;**不碰 DB**;**不感知"上游"**
2. `name: str` 属性 + `async respond(body, *, stream) -> Response` 方法
3. 注册到 ModelRegistry 用 `@register("type")` 装饰 + 实现 `from_config(options)`
   classmethod

---

## 6. 模型管理层(0.3.0 重写)

### 6.1 数据存储

模型配置住 chariot 自己的 SQLite(同 `logs` 表那个 DB)。两张新表:

#### `models` 表

```sql
CREATE TABLE models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,
    options     TEXT NOT NULL,       -- JSON-serialized dict
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- `name` 唯一(用户面 ID,等于 0.2.6 TOML 里的 `[[models]] name`)
- `type` ∈ `ModelRegistry.known_types()`;repo 层 insert/update 时校验
- `options` 按 type schema 形态的 dict,JSON 字符串入库(SQLite 无原生 JSON 列)

#### `settings` 表

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

KV 表,0.3.0 只用一行:`('active_model', '<name>')`。该行不存在 → 走 MockModel
fallback;`<name>` 指向不存在的 entry → 启动 raise(数据不一致,需修)。

### 6.2 类设计

| 类 | 文件 | 职责 |
|---|---|---|
| `ModelRow` ORM | `chariot/server/database/models.py` | sqlalchemy 映射 models 表 |
| `SettingRow` ORM | 同上 | 映射 settings 表 |
| `ModelRepo` | `chariot/server/repository/model_repo.py` | list / get / create / update / delete / duplicate / get_active / set_active;参数校验、JSON 序列化、`seed_if_empty()` |
| `ModelEntry` | `chariot/server/config.py` | 数据形态不变(name / type / options),供 ModelRegistry.build 接口 |
| `ChariotConfig` | 同上 | 数据形态不变;来源从 TOML 文件切到 `ModelRepo`,classmethod `from_db(session) -> ChariotConfig` |
| ~~`ConfigLoader`~~ | ~~同上~~ | **删除**(无文件可 load) |
| `ModelRegistry` | `chariot/server/model/registry.py` | 0.2.x 沿用,无改动 |

### 6.3 启动流程(seed mock)

```python
async with lifespan(_app):
    await init_db()                         # 含 v2 migration
    async with session_maker() as s:
        await ModelRepo.seed_if_empty(s)    # 表空 → seed ('mock', 'mock', '{}')
                                            # + ('active_model', 'mock')
        config = await ChariotConfig.from_db(s)
    Agent.install_from_db(config)
    yield
    Agent.uninstall()
    await dispose_db()
```

`Agent.install_from_db`:

```python
@classmethod
def install_from_db(cls, config: ChariotConfig) -> Agent:
    entry = config.active_entry()
    model = ModelRegistry.build(entry) if entry else mock_model
    return cls.install(model=model)
```

### 6.4 CRUD 后的 Agent 同步

`POST /admin/models/entries` 等写操作完成后,controller 层负责:

1. `ModelRepo.create / update / delete / duplicate` 持久化
2. 重新装载 `ChariotConfig.from_db(session)`(纯读)
3. 触发 `Agent.refresh_config(config)`(只更新 `_config` 缓存,不重建当前 Model 实例 —— 当前 active model 实例继续用,直到 active 被切走或 entry 改了 type/options 才 rebuild)

具体:

| 操作 | 是否需要 rebuild active model 实例 |
|---|---|
| create entry | 否(新 entry 不影响当前 active) |
| update non-active entry | 否 |
| update **active** entry | 是(rebuild active,新 options 即时生效) |
| delete entry | 否(active 不许删,删非 active 不影响) |
| duplicate | 否 |
| switch active(POST /admin/models) | 是(沿用 0.2.x 行为) |

### 6.5 不做老用户迁移

旧 `~/.chariot/config.toml` 不再被读取。该文件保留在用户文件系统不动 ——
chariot 不主动删 / rename / 备份(决策点 3)。0.3.0 假设用户启动后在 Models
页手工重新加 entries(seed 的 `mock` 即开箱可用)。

---

## 7. 表清单

```sql
-- 0.1.0 起,沿用
CREATE TABLE logs ( ... );
CREATE INDEX idx_logs_created_at ON logs(created_at);

-- 0.3.0 新增
CREATE TABLE models ( ... );
CREATE TABLE settings ( ... );
```

migrations 顺序:`v0`(空)→ `v1`(logs)→ `v2`(models + settings + seed mock)。
`PRAGMA user_version` 沿用 0.1.0 的迁移机制。

---

## 8. 管理面

```
GET  /admin/ping       → {ok: true}
GET  /admin/status     → {version, uptime_ms, model, url}
POST /admin/shutdown   → graceful shutdown
GET  /admin/logs       → list LogOut(limit / offset / since)
GET  /admin/stats      → {period, total_requests, success_rate, avg_latency_ms}

GET  /admin/models                       → 列出 entries + active + types(0.2.x 沿用)
POST /admin/models                       → 切换 active(0.2.x 沿用,但 0.3.0 起持久化到 settings)
POST /admin/models/{name}/probe          → 探针(0.2.5 起)

POST   /admin/models/entries             → 0.3.0 新增:create entry(body: {name, type, options})
PUT    /admin/models/entries/{name}      → 0.3.0 新增:update entry(body: {type?, options?})
DELETE /admin/models/entries/{name}      → 0.3.0 新增:delete entry
POST   /admin/models/entries/{name}/duplicate → 0.3.0 新增:复制(body: {as: <new-name>})
```

CRUD 行为细节:

- **create**:校验 name 唯一、type ∈ known_types、options 可 JSON 序列化为 dict;
  重名 → 409 `name_exists`;type 未注册 → 400 `unknown_type`
- **update**:可改 type / options;改 name 走 duplicate + delete 老的(本端点不
  支持改 name,简化);改 active entry → 触发 active model rebuild
- **delete**:active entry 拒绝(400 `cannot_delete_active`,提示先 use 别的)
- **duplicate**:body.as 缺省时默认 `<name>_copy`,碰撞自动加序号(`<name>_copy_2`
  / `_3` ...)

---

## 9. 单例 / lifespan(见 §6.3)

---

## 10. AnthropicModel 实现要点(沿用 0.2.6,见归档 §10)

API key 来源、错误映射、流式中途断开行为均不变。

---

## 11-12. 设计模式 + 测试覆盖

### 设计模式新增(0.3.0)

| 模式 | 用在哪 | 解决什么 |
|---|---|---|
| **Repository** | `ModelRepo` 在 `repository/` 层 | 隔离持久化细节;Agent / Controller 不直接碰 ORM |

其它(Strategy / Registry / Singleton)沿用,见归档 §11。

### 测试覆盖新增

| 文件 | 覆盖 |
|---|---|
| `tests/server/test_model_repo.py` | CRUD + 校验 + duplicate 命名规则 + seed_if_empty |
| `tests/server/test_admin_models.py`(扩) | 5 个新端点 integration |

旧 `tests/server/test_config.py`(TOML loader 测试)→ 改写或删除。

---

## 13. 0.4.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.4.0**:Agent 层多轮记忆 + 工具调用(0.2.6 DESIGN 里的 0.3.0 路标推到 0.4)
- **0.5.0**:进化循环
- **0.6.0**:多 Agent 实例
