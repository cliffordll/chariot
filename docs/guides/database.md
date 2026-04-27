# 数据库使用指南

覆盖 chariot 项目数据库相关的所有日常操作:

- 数据库怎么被创建
- 如何查询 / 插入 / 更新 / 删除数据(业务 CRUD)
- 如何通过 migrations 修改 schema

代码实现:`chariot/server/database/*` + `chariot/server/repository/*`。

---

## 1. 架构速览

| 组件 | 角色 |
|---|---|
| SQLite | 本地文件型 DB,单文件 `~/.chariot/chariot.db`,零运维 |
| **aiosqlite** | 异步驱动,不在代码层 import,只作 SQLAlchemy 驱动后端 |
| **SQLAlchemy 2.x async** | 所有 DB 操作(业务 CRUD + migrations)统一走这层 |
| `chariot/server/database/models.py` | ORM 声明(`LogEntry` / `ModelRow` / `SettingRow`) |
| `chariot/server/database/session.py` | engine / session 工厂 + migration runner |
| `chariot/server/database/migrations/` | `NNN_*.sql` schema 变更文件 |
| `chariot/server/repository/*.py` | 业务 Repo 层(`LogRepo` / `ModelRepo`),把 SA 操作包成领域语义 |

业务路径优先走 Repo;直接拿 `AsyncSession` 写 raw `select(...)` 只在临时脚本 / 还没沉淀 Repo 的场景用。

---

## 2. 数据库怎么被创建

**自动创建,零人工 `createdb`**。SQLite 的 DB 文件首次连接时自动产生;chariot server 启动时自动跑 migrations 生成 schema。

### 启动流程(`init_db()`)

在 `chariot/server/app.py` 的 lifespan 钩子里调用:

```python
async def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)       # 确保 ~/.chariot/ 存在
    engine = create_async_engine(_db_url(db_path))          # 建 engine(DB 文件缺失会自动创建)
    await _maybe_run_migrations(engine)                     # 首启跑 migrations
    _state.engine = engine
    _state.session_maker = async_sessionmaker(engine, expire_on_commit=False)
```

启动后还会立刻跑一次 `ModelRepo.seed_if_empty()`(`models` 表空时插入默认 mock entry + active=mock),保证开箱可用。

默认 DB 路径:`~/.chariot/chariot.db`(Windows 下是 `C:\Users\<you>\.chariot\chariot.db`)。

### 手动初始化(测试 / 脚本)

```python
from pathlib import Path
from chariot.server.database.session import init_db, dispose_db

await init_db(Path("/tmp/test.db"))
# ... 用 DB ...
await dispose_db()
```

### 清库重建

```bash
rm ~/.chariot/chariot.db
uv run python -m chariot.server   # 下次启动自动按最新 migrations 重建 + seed mock entry
```

---

## 3. 连接 DB 的姿势

### 在 HTTP 端点里(推荐)

用 FastAPI 依赖注入自动拿 session:

```python
from chariot.server.database.session import SessionDep   # = Annotated[AsyncSession, Depends(get_session)]


@router.get("/anything")
async def anything(session: SessionDep) -> Response:
    # session 已开好,请求结束自动关
    repo = ModelRepo(session)
    entries = await repo.list_entries()
    return ...
```

### 脱离 FastAPI 上下文(脚本 / 后台任务 / 测试)

```python
from chariot.server.database.session import _state, init_db

if _state.session_maker is None:
    await init_db()

async with _state.session_maker() as session:
    # 用 session
    ...
```

### Session 生命周期要点

- `expire_on_commit=False`:commit 后对象属性不过期,可以继续读字段(否则 SA 默认每次 commit 后 lazy-reload)
- `async with session`:退出自动 rollback(未 commit 的变更)+ close
- 一个 HTTP 请求一个 session:不要跨请求复用

---

## 4. 查询数据(SELECT)

> 业务路径优先走 Repo:`ModelRepo.list_entries()` / `LogRepo.list(...)` 等,内部封装下面这些 SA 用法。
> 下面演示的是**底层模式**,加新 Repo / 新查询时照着写。

### 4.1 查全部

```python
from sqlalchemy import select
from chariot.server.database.models import ModelRow

result = await session.execute(
    select(ModelRow).order_by(ModelRow.created_at.asc(), ModelRow.id.asc())
)
rows = result.scalars().all()         # Sequence[ModelRow]
```

### 4.2 按条件过滤

```python
# SELECT * FROM models WHERE type = 'anthropic'
result = await session.execute(
    select(ModelRow).where(ModelRow.type == "anthropic")
)
rows = result.scalars().all()
```

**组合多个条件**:

```python
from sqlalchemy import and_, or_

# (type = 'anthropic') OR (name LIKE 'claude_%')
stmt = select(ModelRow).where(
    or_(
        ModelRow.type == "anthropic",
        ModelRow.name.like("claude_%"),
    )
)
```

### 4.3 按主键

ChariotORM 主键策略**两种并存**,别混:

| 表 | 主键 | 类型 | 怎么拿 |
|---|---|---|---|
| `LogEntry` | `id` | 32 字符 UUID4 hex(`default=uuid4().hex`) | `session.get(LogEntry, "<32-char hex>")` |
| `ModelRow` | `id` | 自增 int | `session.get(ModelRow, 1)` —— 但业务上**别这么查** |
| `ModelRow` | `name` | 唯一索引(用户面 ID) | `select(ModelRow).where(ModelRow.name == "claude")` |
| `SettingRow` | `key` | str | `session.get(SettingRow, "active_model")` |

`ModelRow.id` 自增 int 是 ORM 内部 PK,业务上唯一标识用 `name`(带 `unique=True` + 索引)。

```python
# 业务里查单条 entry 总是按 name
stmt = select(ModelRow).where(ModelRow.name == "claude")
row = (await session.execute(stmt)).scalar_one_or_none()
if row is None:
    raise HTTPException(404, "not found")
```

### 4.4 单标量值

```python
from sqlalchemy import func

# 数行数(ModelRepo.count() 已封装)
result = await session.execute(select(func.count(ModelRow.id)))
count: int = result.scalar_one()

# 存在性检查
result = await session.execute(
    select(ModelRow.id).where(ModelRow.name == "claude")
)
exists = result.scalar_one_or_none() is not None
```

### 4.5 排序 / 分页

```python
# 最近 10 条 logs
stmt = (
    select(LogEntry)
    .order_by(LogEntry.created_at.desc())
    .offset(20)
    .limit(10)
)
result = await session.execute(stmt)
page = result.scalars().all()
```

### 4.6 原生 SQL(少用)

```python
from sqlalchemy import text

result = await session.execute(
    text("SELECT name FROM models WHERE type = :t"),
    {"t": "anthropic"},
)
names = [row[0] for row in result]
```

用 `text()` 时**必须**用参数绑定(`:t`),不要字符串拼接,否则有 SQL 注入风险。

> 当前 ORM 没声明 `relationship(...)`,所以暂不展示 `selectinload` / N+1 防护;真要加跨表关系时再扩这一节。

---

## 5. 插入 · 更新 · 删除

### 5.1 插入

```python
import json
from chariot.server.database.models import ModelRow

row = ModelRow(
    name="claude",
    type="anthropic",
    options=json.dumps({"model": "claude-opus-4-5", "api_key": "sk-x"}),
)
session.add(row)
await session.commit()           # 必须 commit,否则不落盘
await session.refresh(row)       # 拿回 DB 生成的 id / created_at / updated_at
print(row.id, row.created_at)
```

`ModelRow.options` 是 JSON-serialized text(SA `Mapped[str]`),写入前手 dump、读出时 `json.loads`。这一步 `ModelRepo._serialize_options / _deserialize_options` 已经包好,业务直接用 Repo 即可。

**批量插入**:

```python
session.add_all([ModelRow(...), ModelRow(...), ModelRow(...)])
await session.commit()
```

### 5.2 更新字段

**方式 A · ORM(先加载再改)**:

```python
stmt = select(ModelRow).where(ModelRow.name == "claude")
row = (await session.execute(stmt)).scalar_one_or_none()
if row is None:
    raise HTTPException(404)

row.type = "anthropic"
row.options = json.dumps({"model": "claude-haiku-4-5"})
await session.commit()           # SA 自动生成 UPDATE SQL,onupdate=_utcnow 也会刷 updated_at
```

**方式 B · Core UPDATE(不加载,批量)**:

```python
from sqlalchemy import update

stmt = (
    update(ModelRow)
    .where(ModelRow.type == "anthropic")
    .values(options=json.dumps({"model": "claude-opus-4-5"}))
)
result = await session.execute(stmt)
await session.commit()
print(result.rowcount)          # 被影响的行数
```

**区别**:
- A 适合"单条记录按业务逻辑改" — 直观,能跑 ORM 事件钩子(如 `onupdate=_utcnow`)
- B 适合"批量刷一刀" — 不加载对象进内存,SQL 更直接,但**不会触发 `onupdate=...`**

### 5.3 删除

```python
# ORM
stmt = select(ModelRow).where(ModelRow.name == "claude")
row = (await session.execute(stmt)).scalar_one_or_none()
if row:
    await session.delete(row)
    await session.commit()

# Core
from sqlalchemy import delete
await session.execute(delete(ModelRow).where(ModelRow.name == "claude"))
await session.commit()
```

### 5.4 事务回滚

```python
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

try:
    session.add(ModelRow(name="duplicate", type="mock", options="{}"))
    await session.commit()
except IntegrityError as e:
    await session.rollback()     # 回滚,session 依然可继续用
    raise HTTPException(409, "model name 已存在") from e
```

`ModelRepo.create()` 已经把这个模式封好,业务里直接 catch `DuplicateModelName`(语义层异常)就好。

FastAPI 的 `SessionDep` 依赖在请求异常退出时会自动 rollback,业务代码里只在需要"立即 rollback + 继续做其他事"时显式调。

---

## 6. Migrations(改 schema)

### 6.1 为什么需要

**Migrations = schema 变更日志**,让 DB 跟随代码演进,升级时保留既有数据。

典型场景:0.2.x 的 DB 只有 `logs` 表,0.3.0 要加 `models` / `settings` 表存模型配置:
- 不管 → 代码读不存在的表 → 运行时 `no such table` 报错
- 删库重建 → 数据丢
- **Migration** → `CREATE TABLE models...` + `CREATE TABLE settings...`,数据保留 ✓

### 6.2 整体机制

`chariot/server/database/migrations/` 下放 `NNN_*.sql` 文件,runner 按 `PRAGMA user_version` 自动增量跑:

- `001_init.sql` 建初始 schema(`logs` 表 + 索引),末尾 `PRAGMA user_version = 1;`
- `002_models_settings.sql` 建 `models` / `settings` 表,末尾 `PRAGMA user_version = 2;`
- runner(`session.py::_maybe_run_migrations`)启动时:
  1. 扫目录拿所有 `[0-9][0-9][0-9]_*.sql`
  2. 自检 `CURRENT_SCHEMA_VERSION == 最高 migration 编号`(防止代码/文件不一致)
  3. 读 DB 的 `user_version = current`
  4. 若 `current > CURRENT_SCHEMA_VERSION` → 报错拒启动(老 server 碰新 DB)
  5. 按顺序跑 `N > current` 的文件,每个独立事务

### 6.3 加表的完整 5 步(`002_models_settings.sql` 是真实参考)

#### 步骤 1:写 migration 文件

`chariot/server/database/migrations/002_models_settings.sql`(0.3.0 真实存在):

```sql
-- v2 · 0.3.0 模型配置 DB 化:加 models / settings 表

CREATE TABLE models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    type        TEXT    NOT NULL,
    options     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_models_name ON models(name);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

PRAGMA user_version = 2;
```

**要点**:
- 文件名前 3 位严格数字(`002`,不是 `2` / `02`)
- 最后一行 `PRAGMA user_version = N;` **不可省**
- 整行 `--` 注释会被 runner 过滤,行内 `--` 由 SQLite 自己处理

#### 步骤 2:加 ORM(`models.py`)

```python
class ModelRow(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    type: Mapped[str]
    options: Mapped[str]   # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
```

#### 步骤 3:递增版本常量(`session.py`)

```python
CURRENT_SCHEMA_VERSION = 2   # 1 → 2
```

忘改 → 启动时自检报错:

```
CURRENT_SCHEMA_VERSION=1 与最高 migration 编号 2 不一致
```

#### 步骤 4(可选):加 Repo + 暴露到 API

新表通常配一个 `Repo`(`chariot/server/repository/model_repo.py`)封装常用查询/写入,
controller 通过 `SessionDep` 注入 session 后调 Repo,而不是直接拼 `select(...)`。

#### 步骤 5:起 server 自动升级

```bash
uv run python -m chariot.server
```

- 老用户 DB(`user_version=1`)→ runner 跑 002 → 到 2,`logs` 数据保留
- 新用户空 DB(`user_version=0`)→ runner 跑 001+002,一次到位
- 启动后 `ModelRepo.seed_if_empty()` 给空 `models` 表插 mock entry + active=mock

### 6.4 其他常见操作

#### 加字段

```sql
-- 003_add_models_version_tag.sql(假设场景)
ALTER TABLE models ADD COLUMN version_tag TEXT;

PRAGMA user_version = 3;
```

#### 加索引

```sql
-- 004_add_models_type_index.sql
CREATE INDEX idx_models_type ON models(type);

PRAGMA user_version = 4;
```

#### 改字段名(现代 SQLite)

SQLite 3.35+(chariot 运行环境都够):

```sql
-- 005_rename_options_to_config.sql(假设某次真要改)
ALTER TABLE models RENAME COLUMN options TO config;

PRAGMA user_version = 5;
```

#### 数据迁移(backfill)

```sql
-- 006_backfill_active_model.sql(假设)
INSERT OR IGNORE INTO settings (key, value) VALUES ('active_model', 'mock');

PRAGMA user_version = 6;
```

### 6.5 约束与坑

- **NNN 编号**:3 位数字,0-999,不能重复(runner 会报错)
- **PRAGMA 位置**:必须放文件最后一行,否则中间失败会导致"版本标记提前"
- **单文件一事务**:文件内任一语句失败 → 整文件回滚;跨多个 migration 不保证原子性
- **不支持 downgrade**:forward-only;降级靠 `git checkout <旧版> + rm DB` 重来
- **SQL 解析**:runner 按 `;` 切多语句,不处理字符串字面量里的 `;`(如 `INSERT ... VALUES ('a;b')`);遇到请拆成多条 SQL

### 6.6 Migration vs 业务 UPDATE(容易混)

| 维度 | Migration | 业务 UPDATE |
|---|---|---|
| 改什么 | schema(表 / 列 / 索引) | 数据(某些行的字段值) |
| 何时跑 | server 启动时一次,自动 | HTTP 请求里随时 |
| 写在哪 | `NNN_*.sql` 文件,纯 SQL | `repository/*.py`,走 SA session |
| 能用 ORM 吗 | 不能(ORM 跟当前代码走,不稳) | 应该用 |

举例:给 `models` 加 `version_tag` 列是 migration;把 `models.name='claude'` 那行的 `options` 改成新 JSON 是业务 UPDATE。

---

## 7. 调试 / 常用命令

### 直接打开 DB 看

```bash
uv run python -c "
import sqlite3
from pathlib import Path
c = sqlite3.connect(str(Path.home()/'.chariot/chariot.db'))
print('user_version =', c.execute('PRAGMA user_version').fetchone()[0])
print('tables:', [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])
for t in ('logs', 'models', 'settings'):
    cols = [r[1] for r in c.execute(f'PRAGMA table_info({t})').fetchall()]
    print(f'{t}:', cols)
"
```

### 清库重来

```bash
rm ~/.chariot/chariot.db
uv run python -m chariot.server
```

### 让 SA 打印实际 SQL(调试慢查询)

临时在 `init_db` 里:

```python
engine = create_async_engine(_db_url(db_path), echo=True)  # echo=True 打印所有 SQL
```

验完删掉或改配置开关,不要留下来污染日志。

### 查事务哪里卡住了

SQLite 单写并发:一个写事务未 commit,其他写阻塞。打印连接池状态:

```python
print(engine.pool.status())
```

---

## 8. FAQ

**Q:能不能用 Alembic?**
A:可以,但不引入。Alembic 适合复杂团队 / 多后端 / 需要 down migration 的场景;本项目单用户本地 SQLite,手写 SQL 够用。

**Q:加字段后老 client 报错吗?**
A:不会。老 client 调返回的 JSON 里有新字段它自己忽略。破坏性变更(删字段 / 改类型)才需考虑兼容。

**Q:迁移写错了怎么办?**
A:
- 未 push → 改文件 / `rm DB` / 重启
- 已 push(用户升级过)→ **不要改原文件**(改了老用户升级时会漏修正);写 `NNN+1_fix_xxx.sql` 做补丁。这是"migration 历史 immutable"原则

**Q:可以写 Python migration 脚本吗?**
A:当前 runner 只认 SQL。真要复杂 Python 数据变换时扩展 runner 让它也认 `NNN_*.py`,~10 行代码。YAGNI,没真需求不加。

**Q:`ModelRow.options` 为啥是 TEXT 而不是 JSON 类型?**
A:SQLite 有 JSON1 扩展,但跨平台支持参差(打包到 PyInstaller 的 sqlite3 不一定带);存 TEXT + 应用层 `json.dumps/loads` 最稳。`ModelRepo._serialize_options / _deserialize_options` 已经把这层包好,业务代码不用直面。

**Q:session 和 connection 有啥区别?**
A:
- **connection**:TCP 到 SQLite 文件的句柄
- **session**:基于 connection 的高层抽象,管事务边界、对象状态、identity map(同一 id 的 ModelRow 对象在一个 session 里只有一份)
- 业务代码用 session 就好,connection 只在 `init_db` 跑 migrations 时直接用
