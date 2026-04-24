# 数据库使用指南

> **⚠️ 示例代码含 Rosetta 遗留**:文中的 `Upstream` / `UpstreamRepo` 等例子来自
> 从 Rosetta 迁来的阶段,chariot v0 已删掉 upstream 表,现只剩 `LogEntry` /
> `LogRepo`。SQLAlchemy 2.x async / select / get / count 等**模式**仍然适用,
> 照抄时请把具体类名替换为 `LogEntry` / `LogRepo`。
>
> 覆盖 chariot 项目数据库相关的所有日常操作:
> - 数据库怎么被创建
> - 如何查询 / 插入 / 更新 / 删除数据(业务 CRUD)
> - 如何通过 migrations 修改 schema

代码实现:`chariot/server/database/*`。

---

## 1. 架构速览

| 组件 | 角色 |
|---|---|
| SQLite | 本地文件型 DB,单文件 `~/.chariot/chariot.db`,零运维 |
| **aiosqlite** | 异步驱动,不在代码层 import,只作 SQLAlchemy 驱动后端 |
| **SQLAlchemy 2.x async** | 所有 DB 操作(业务 CRUD + migrations)统一走这层 |
| `chariot/server/database/models.py` | ORM 声明(`Upstream` / `LogEntry`) |
| `chariot/server/database/session.py` | engine / session 工厂 + migration runner |
| `chariot/server/database/migrations/` | `NNN_*.sql` schema 变更文件 |

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
uv run python -m chariot.server   # 下次启动自动按最新 migrations 重建
```

---

## 3. 连接 DB 的姿势

### 在 HTTP 端点里(推荐)

用 FastAPI 依赖注入自动拿 session:

```python
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.database.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/anything")
async def anything(session: SessionDep) -> Response:
    # session 已开好,请求结束自动关
    result = await session.execute(...)
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

### 4.1 查全部

```python
from sqlalchemy import select
from chariot.server.database.models import Upstream

result = await session.execute(select(Upstream).order_by(Upstream.id))
upstreams = result.scalars().all()         # Sequence[Upstream]
```

### 4.2 按条件过滤

```python
# SELECT * FROM upstreams WHERE provider = 'anthropic' AND enabled = 1
result = await session.execute(
    select(Upstream)
    .where(Upstream.provider == "anthropic")
    .where(Upstream.enabled.is_(True))
)
rows = result.scalars().all()
```

**组合多个条件**:

```python
from sqlalchemy import and_, or_

# (provider = 'openai' AND enabled) OR provider = 'custom'
stmt = select(Upstream).where(
    or_(
        and_(Upstream.provider == "openai", Upstream.enabled.is_(True)),
        Upstream.provider == "custom",
    )
)
```

### 4.3 按主键

```python
# v0 起 Upstream.id 是 32 字符 UUID4 hex(TEXT),不是自增 INTEGER
upstream = await session.get(Upstream, "00000000000000000000000000000000")
if upstream is None:
    raise HTTPException(404, "not found")
```

### 4.4 单标量值

```python
from sqlalchemy import func

# 数行数
result = await session.execute(select(func.count()).select_from(Upstream))
count: int = result.scalar_one()

# 存在性检查
result = await session.execute(
    select(Upstream.id).where(Upstream.name == "x")
)
exists = result.scalar_one_or_none() is not None
```

### 4.5 排序 / 分页

```python
stmt = (
    select(LogEntry)
    .order_by(LogEntry.created_at.desc())
    .offset(20)
    .limit(10)
)
result = await session.execute(stmt)
page = result.scalars().all()
```

### 4.6 关联查询(避免 N+1)

```python
from sqlalchemy.orm import selectinload

# 取 logs,同时预加载每条的 upstream,避免 N 次额外 SQL
result = await session.execute(
    select(LogEntry)
    .options(selectinload(LogEntry.upstream))
    .order_by(LogEntry.created_at.desc())
    .limit(20)
)
logs = result.scalars().all()
# logs[0].upstream.name 直接可访问,无额外 SQL
```

**注**:需要在 `LogEntry` 模型里先声明 `relationship("Upstream")`,当前 v0 还没加(按需)。

### 4.7 原生 SQL(少用)

```python
from sqlalchemy import text

result = await session.execute(
    text("SELECT name FROM upstreams WHERE provider = :p"),
    {"p": "anthropic"},
)
names = [row[0] for row in result]
```

用 `text()` 时**必须**用参数绑定(`:t`),不要字符串拼接,否则有 SQL 注入风险。

---

## 5. 插入 · 更新 · 删除

### 5.1 插入

```python
upstream = Upstream(
    name="my-upstream",
    protocol="messages",      # messages / completions / responses
    provider="anthropic",     # anthropic / openai / openrouter / ... / custom / mock
    base_url="https://api.anthropic.com",  # v0 必填,不再按 provider 取默认
    api_key="sk-x",           # 可选:留 None 表示客户端自带 x-api-key 透传
)
session.add(upstream)
await session.commit()           # 必须 commit,否则不落盘
await session.refresh(upstream)  # 拿回 DB 生成的 id 和 created_at
print(upstream.id)               # 32 字符 UUID4 hex(ORM default= uuid4().hex)
```

**批量插入**:

```python
session.add_all([Upstream(...), Upstream(...), Upstream(...)])
await session.commit()
```

### 5.2 更新字段

**方式 A · ORM(先加载再改)**:

```python
upstream = await session.get(Upstream, "00000000000000000000000000000000")
if upstream is None:
    raise HTTPException(404)

upstream.enabled = False
upstream.api_key = "sk-new"
await session.commit()           # SA 自动生成 UPDATE SQL
```

**方式 B · Core UPDATE(不加载,批量)**:

```python
from sqlalchemy import update

stmt = (
    update(Upstream)
    .where(Upstream.provider == "custom")
    .values(enabled=False)
)
result = await session.execute(stmt)
await session.commit()
print(result.rowcount)          # 被影响的行数
```

**区别**:
- A 适合"单条记录按业务逻辑改" — 直观,能跑 ORM 事件钩子
- B 适合"批量刷一刀" — 不加载对象进内存,SQL 更直接

### 5.3 删除

```python
# ORM
upstream_id = "00000000000000000000000000000000"
upstream = await session.get(Upstream, upstream_id)
if upstream:
    await session.delete(upstream)
    await session.commit()

# Core
from sqlalchemy import delete
await session.execute(delete(Upstream).where(Upstream.id == upstream_id))
await session.commit()
```

### 5.4 事务回滚

```python
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

try:
    session.add(Upstream(name="duplicate", ...))
    await session.commit()
except IntegrityError as e:
    await session.rollback()     # 回滚,session 依然可继续用
    raise HTTPException(409, "name 已存在") from e
```

FastAPI 的 `SessionDep` 依赖在请求异常退出时会自动 rollback,业务代码里只在需要"立即 rollback + 继续做其他事"时显式调。

---

## 6. Migrations(改 schema)

### 6.1 为什么需要

**Migrations = schema 变更日志**,让 DB 跟随代码演进,升级时保留既有数据。

典型场景:v0.1 已有用户 20 条 upstream 记录,v0.2 要加 `last_used_at` 字段:
- 不管 → 代码用新字段,DB 没有 → 运行时 `no such column` 报错
- 删库重建 → 数据丢
- **Migration** → `ALTER TABLE upstreams ADD COLUMN ...`,数据保留 ✓

### 6.2 整体机制

`chariot/server/database/migrations/` 下放 `NNN_*.sql` 文件,runner 按 `PRAGMA user_version` 自动增量跑:

- `001_init.sql` 建初始 schema,末尾 `PRAGMA user_version = 1;`
- `002_xxx.sql` 的末尾 `PRAGMA user_version = 2;`
- runner(`session.py::_maybe_run_migrations`)启动时:
  1. 扫目录拿所有 `[0-9][0-9][0-9]_*.sql`
  2. 自检 `CURRENT_SCHEMA_VERSION == 最高 migration 编号`(防止代码/文件不一致)
  3. 读 DB 的 `user_version = current`
  4. 若 `current > CURRENT_SCHEMA_VERSION` → 报错拒启动(老 server 碰新 DB)
  5. 按顺序跑 `N > current` 的文件,每个独立事务

### 6.3 加字段的完整 5 步(以 `upstreams.last_used_at` 为例)

#### 步骤 1:写 migration 文件

`chariot/server/database/migrations/002_add_last_used_at.sql`:

```sql
-- v0.2 · 给 upstreams 加 last_used_at 字段

ALTER TABLE upstreams ADD COLUMN last_used_at TEXT;

PRAGMA user_version = 2;
```

**要点**:
- 文件名前 3 位严格数字(`002`,不是 `2` / `02`)
- 最后一行 `PRAGMA user_version = N;` **不可省**
- 整行 `--` 注释会被 runner 过滤,行内 `--` 由 SQLite 自己处理

#### 步骤 2:改 ORM(`models.py`)

```python
class Upstream(Base):
    ...
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
```

#### 步骤 3:递增版本常量(`session.py`)

```python
CURRENT_SCHEMA_VERSION = 2   # 1 → 2
```

忘改 → 启动时自检报错:

```
CURRENT_SCHEMA_VERSION=1 与最高 migration 编号 2 不一致
```

#### 步骤 4(可选):暴露到 API(`admin/upstreams.py`)

```python
class UpstreamOut(BaseModel):
    ...
    last_used_at: datetime | None
```

敏感字段(`api_key`)**不应**出现在 `UpstreamOut` 里。

#### 步骤 5:起 server 自动升级

```bash
uv run python -m chariot.server
```

- 老用户 DB(`user_version=1`)→ runner 跑 002 → 到 2,数据保留
- 新用户空 DB(`user_version=0`)→ runner 跑 001+002,一次到位

### 6.4 其他常见操作

#### 加表

```sql
-- 003_add_conversations.sql
-- 项目惯例:id 用 TEXT UUID4 hex(ORM default=uuid4().hex),不用 AUTOINCREMENT
CREATE TABLE conversations (
    id         TEXT    PRIMARY KEY,
    title      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_created_at ON conversations(created_at);

PRAGMA user_version = 3;
```

#### 加索引

```sql
-- 004_add_upstreams_enabled_index.sql
CREATE INDEX idx_upstreams_enabled ON upstreams(enabled);

PRAGMA user_version = 4;
```

#### 改字段名(现代 SQLite)

SQLite 3.35+(chariot 运行环境都够):

```sql
-- 005_rename_base_url_to_endpoint.sql(假设某次真要改)
ALTER TABLE upstreams RENAME COLUMN base_url TO endpoint;

PRAGMA user_version = 5;
```

#### 数据迁移(backfill)

```sql
-- 006_backfill_enabled.sql
UPDATE upstreams SET enabled = 1 WHERE enabled IS NULL;

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
| 写在哪 | `NNN_*.sql` 文件,纯 SQL | `admin/*.py`,走 SA session |
| 能用 ORM 吗 | 不能(ORM 跟当前代码走,不稳) | 应该用 |

举例:给 `upstreams` 加 `last_used_at` 列是 migration;把 upstream=5 的 `last_used_at` 改成 `NOW()` 是业务 UPDATE。

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
for t in ('upstreams', 'logs'):
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
A:可以,但 v0 不引入。Alembic 适合复杂团队 / 多后端 / 需要 down migration 的场景;本项目单用户本地 SQLite,手写 SQL 够用。

**Q:加字段后老 client 报错吗?**
A:不会。老 client 调返回的 JSON 里有新字段它自己忽略。破坏性变更(删字段 / 改类型)才需考虑兼容。

**Q:迁移写错了怎么办?**
A:
- 未 push → 改文件 / `rm DB` / 重启
- 已 push(用户升级过)→ **不要改原文件**(改了老用户升级时会漏修正);写 `NNN+1_fix_xxx.sql` 做补丁。这是"migration 历史 immutable"原则

**Q:可以写 Python migration 脚本吗?**
A:当前 runner 只认 SQL。真要复杂 Python 数据变换时扩展 runner 让它也认 `NNN_*.py`,~10 行代码。YAGNI,没真需求不加。

**Q:session 和 connection 有啥区别?**
A:
- **connection**:TCP 到 SQLite 文件的句柄
- **session**:基于 connection 的高层抽象,管事务边界、对象状态、identity map(同一 id 的 Upstream 对象在一个 session 里只有一份)
- 业务代码用 session 就好,connection 只在 `init_db` 跑 migrations 时直接用
