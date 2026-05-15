"""SQLAlchemy 声明式 ORM 模型。

与 `migrations/*.sql` 字段对齐;SQL 是 schema 真源,ORM 镜像它。

- v1:`logs` 表(请求流水)
- v2(0.3.0):`models` 表 + `settings`(KV)—— 模型配置 DB 化
- v3(0.3.1):`models` 加 `params` 列;drop `settings` 表(active 概念删除,
  client 在 body.model 写 entry name 直接路由)
- v4(0.4.0):加 `conversations / messages / tools` 三表 —— 多轮会话层 +
  Tool 层。messages.role 仅 `user / assistant`(Anthropic 协议原生两种),
  tool_use / tool_result 嵌入 content blocks 数组;tools 4 条 seeded fixture
- v5(0.6.0):`models` rename → `providers`,`messages.model_name` rename →
  `provider_name`,`logs.model` rename → `provider`(0.6.0 抽象层已是
  BaseProvider,DB 层跟上;`ChatRequest.model` / options.model 仍叫 model
  对齐 Claude API)
- v6(0.6.0):`providers` 加 `is_default` 列(同 commit 落 `ProviderRepo.get_default
  / set_default / unset_default` + CLI `provider use` / `provider show` 命令)
- v7(0.6.5+):平台基础设施最小落点 —— `memories / eval_runs / eval_cases /
  audit_events / checkpoints / skills`
- v8(0.6.5+):`messages.id` 从自增 int 升到文本主键;新写入消息直接用 ULID
 - v9(0.7.1-prompt):`prompt_bundles / prompt_versions / prompt_traces`
 - v10(0.7.1-prompt):`prompt_bundles.is_active / prompt_versions.is_active`
- v11(0.7.1-context):`context_snapshots / context_traces`
- v14(0.7.2-task):`agent_profiles / tasks / task_runs / scheduled_jobs`
- v15(0.7.2-task):`job_runs`
- v16(0.7.2-tool):`toolsets / toolset_members`(命名 toolset + agent 绑定)
- v17(0.8.0-evolution):`trace_turns / trace_provider_calls / trace_tool_calls / trace_checkpoints`(Phase B1 trace 平台)
主键:
- `LogEntry.id` 是 32 字符 UUID4 hex(`default=` 插入时生成)
- `ProviderRow.id` / `ToolRow.id` 是自增 int(name 才是用户面 ID)
- `ConversationRow.id` / `MessageRow.id` 是 26 字符 ULID 字符串(历史迁移前的旧
  message 行会保留 legacy 文本 id)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID

LogStatus = Literal["ok", "error", "timeout"]


def _new_id() -> str:
    """32 字符 UUID4 hex(无连字符)。"""
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_ulid() -> str:
    """26 字符 ULID 字面量。"""
    return str(ULID())


class Base(DeclarativeBase):
    pass


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_id)
    provider: Mapped[str | None] = mapped_column(default=None)
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[str]
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    __table_args__ = (Index("idx_logs_created_at", "created_at"),)


class ProviderRow(Base):
    """`providers` 表(v6 起;v2 ~ v5 时叫 `models`):承载 BaseProvider 配置 entries。

    - `options`:JSON,build Provider 实例所需参数(LLM model id / api_key /
      base_url 等);`options.model` 字段(对应 Claude API 的 `body.model`)是
      Anthropic SDK 透传字段,不在重命名范围
    - `params`:JSON,runtime sampling 默认值(temperature / top_p / max_tokens 等),
      0.3.1 加;客户端发请求时若 body 缺字段,前端从此处填(server 不主动注入)
    - `is_default`(v7 起):0/1;`chariot chat` 不传 `--provider` 时走默认行
      (是 `is_default=1` 那条)。约束:同时至多一行为 1(由 `ProviderRepo.set_default`
      原子保证 —— 清所有 + 置选中)
    """

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    type: Mapped[str]
    options: Mapped[str]  # JSON-serialized dict
    params: Mapped[str]  # JSON-serialized dict;migration v3 列默认 '{}'
    is_default: Mapped[int] = mapped_column(default=0)  # 0/1;v7 起 schema 落,逻辑后续 commit
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ConversationRow(Base):
    """`conversations` 表(v24 起):多轮对话单元。

    - `id`:26 字符 ULID(client 或 server 生成,正则校验在 controller 层做);
      ULID 单调时间戳前缀让 `ORDER BY id` 即时间序
    - `title`:可选;空则 GUI 从首条 user msg 截取展示
    - `agent_profile`:v24 新增;记录该对话上次使用的 agent profile name
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(default=None)
    agent_profile: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class MessageRow(Base):
    """`messages` 表:按 Anthropic 协议 message 切行,对齐 Claude Code transcript。

    - `role`:`'user' | 'assistant'`(协议原生两种);tool_use 嵌在 assistant.content
      blocks,tool_result 嵌在 user.content blocks
    - `content`:JSON,原样存 anthropic content(字符串或 blocks 数组);
      `SELECT * ORDER BY seq` 直接构成 Anthropic messages 数组,零翻译成本
    - `seq`:会话内单调 0 起;`(conversation_id, seq)` UNIQUE
    - `provider_name`:仅 role='assistant' 行非空,记本轮用的 provider entry name
      (v6 起;v4~v5 时叫 `model_name`)
    - 不设 FK:cascade delete 由 `ConversationRepo.delete()` 手动 DELETE FROM messages
      WHERE conversation_id = ?,行为不依赖 SQLite PRAGMA foreign_keys 全局开关
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    conversation_id: Mapped[str] = mapped_column(index=True)
    seq: Mapped[int]
    role: Mapped[str]  # 'user' | 'assistant'
    content: Mapped[str]  # JSON-serialized;str 或 list[dict]
    provider_name: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ToolRow(Base):
    """`tools` 表:0.4.0 内置工具配置 + 0.8.7 自定义工具。

    0.8.7 开放 CRUD —— builtin 只读,custom 可增删改。
    `name` 即用户面 ID,也是 ToolRegistry 的 type key。
    全部 seeded 默认 `enabled=0`,用户必须显式打开。
    """

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    type: Mapped[str]
    enabled: Mapped[int]  # 0/1;SQLite 无 BOOL 类型,统一用 int
    options: Mapped[str]  # JSON-serialized dict;migration v4 列默认 '{}'
    source: Mapped[str] = mapped_column(default="builtin")  # 'builtin' | 'custom'
    description: Mapped[str] = mapped_column(default="")
    custom_type: Mapped[str | None] = mapped_column(default=None)  # 'http_custom' | 'shell_custom'
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class MemoryRow(Base):
    """`memories` 表:v8 起的长期记忆最小物理基础。"""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    kind: Mapped[str] = mapped_column(index=True)
    text: Mapped[str]
    meta: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    is_pinned: Mapped[int] = mapped_column(default=0)
    is_archived: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class MemoryEventRow(Base):
    """`memory_events` 琛?v13 璧风殑 memory 鏁版嵁鍙樺寲璁板綍銆?"""

    __tablename__ = "memory_events"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    memory_id: Mapped[str] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(index=True)
    payload: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class MemoryLinkRow(Base):
    """`memory_links` 琛?v13 璧风殑 memory 关联銆?"""

    __tablename__ = "memory_links"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    memory_id: Mapped[str] = mapped_column(index=True)
    link_type: Mapped[str] = mapped_column(index=True)
    link_value: Mapped[str] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class EvalRunRow(Base):
    """`eval_runs` 表:v8 起的评估运行记录最小基础。"""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    name: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(index=True)
    summary: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class EvalCaseRow(Base):
    """`eval_cases` 表:v8 起的评估样例最小基础。"""

    __tablename__ = "eval_cases"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    suite: Mapped[str | None] = mapped_column(default=None, index=True)
    name: Mapped[str]
    input_payload: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    expected: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    meta: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class AuditEventRow(Base):
    """`audit_events` 表:v8 起的审计事件最小基础。"""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    event_type: Mapped[str] = mapped_column(index=True)
    status: Mapped[str | None] = mapped_column(default=None, index=True)
    payload: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class CheckpointRow(Base):
    """`checkpoints` 表:v8 起的 checkpoint 最小基础。"""

    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    name: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(index=True)
    target: Mapped[str | None] = mapped_column(default=None)
    payload: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class SkillRow(Base):
    """`skills` 表:v8 起的 skill 注册最小基础。"""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    description: Mapped[str | None] = mapped_column(default=None)
    content: Mapped[str] = mapped_column(default="")
    enabled: Mapped[int] = mapped_column(default=1)
    meta: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class CapabilityRow(Base):
    """`capabilities` 表(v21 起):capability gating 持久化(B5 wave 3)。

    `name` 是主键(已知 capability 名集合有限);默认两条:`enable_self_mod` /
    `yolo`,都 disabled。CLI `--yolo` 全局 flag 是 per-process 覆盖,不写 DB。
    """

    __tablename__ = "capabilities"

    name: Mapped[str] = mapped_column(primary_key=True)
    enabled: Mapped[int] = mapped_column(default=0)  # 0/1
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ProviderHealthRow(Base):
    """`provider_health` 表:v12 起的 provider 最近健康状态。"""

    __tablename__ = "provider_health"

    provider_name: Mapped[str] = mapped_column(primary_key=True)
    last_ok: Mapped[int] = mapped_column(default=1)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    last_probe_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class PromptBundleRow(Base):
    """`prompt_bundles` 琛?prompt system 鐨勫畾涔夎〃銆?"""

    __tablename__ = "prompt_bundles"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    description: Mapped[str | None] = mapped_column(default=None)
    layers: Mapped[str] = mapped_column(default="[]")  # JSON-serialized list
    is_active: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class PromptVersionRow(Base):
    """`prompt_versions` 琛?prompt bundle 鐨勭増鏈埅闈綋銆?"""

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    bundle_id: Mapped[str] = mapped_column(index=True)
    version: Mapped[str] = mapped_column(index=True)
    spec: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    is_active: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class PromptTraceRow(Base):
    """`prompt_traces` 琛?turn 鐨勭粓鏋滃拰鏉ユ簮蹇呯暀銆?"""

    __tablename__ = "prompt_traces"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    bundle_id: Mapped[str] = mapped_column(index=True)
    version_id: Mapped[str] = mapped_column(index=True)
    conversation_id: Mapped[str | None] = mapped_column(default=None, index=True)
    provider_name: Mapped[str] = mapped_column(index=True)
    model: Mapped[str | None] = mapped_column(default=None)
    request: Mapped[str] = mapped_column(default="{}")  # JSON-serialized ChatRequest
    source_refs: Mapped[str] = mapped_column(default="[]")  # JSON-serialized list
    prompt_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ContextSnapshotRow(Base):
    __tablename__ = "context_snapshots"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    conversation_id: Mapped[str | None] = mapped_column(default=None, index=True)
    provider_name: Mapped[str] = mapped_column(index=True)
    model: Mapped[str | None] = mapped_column(default=None)
    request: Mapped[str] = mapped_column(default="{}")
    slices: Mapped[str] = mapped_column(default="[]")
    source_refs: Mapped[str] = mapped_column(default="[]")
    context_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ContextTraceRow(Base):
    __tablename__ = "context_traces"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    snapshot_id: Mapped[str] = mapped_column(index=True)
    conversation_id: Mapped[str | None] = mapped_column(default=None, index=True)
    provider_name: Mapped[str] = mapped_column(index=True)
    model: Mapped[str | None] = mapped_column(default=None)
    prompt_trace_id: Mapped[str | None] = mapped_column(default=None, index=True)
    policy: Mapped[str] = mapped_column(default="{}")
    selected_refs: Mapped[str] = mapped_column(default="[]")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class AgentProfileRow(Base):
    __tablename__ = "agent_profiles"

    name: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(index=True)
    prompt_bundle: Mapped[str | None] = mapped_column(default=None)
    tool_profile: Mapped[str | None] = mapped_column(default=None)
    provider_profile: Mapped[str | None] = mapped_column(default=None)
    budget: Mapped[str] = mapped_column(default="{}")
    meta: Mapped[str] = mapped_column(default="{}")
    # B4 wave 3:reflection 开关 + retry 预算(默认关,跟 ChatRequest 默认对齐)
    reflection_enabled: Mapped[int] = mapped_column(default=0)
    reflection_max_retries: Mapped[int] = mapped_column(default=2)
    # B6 wave 2:agent_profile 预绑 skill;NULL = 不绑(常态)
    default_skill: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    goal: Mapped[str]
    kind: Mapped[str] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(index=True)
    agent_profile: Mapped[str | None] = mapped_column(default=None, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(default=None, index=True)
    owner: Mapped[str | None] = mapped_column(default=None)
    meta: Mapped[str] = mapped_column(default="{}")
    artifacts: Mapped[str] = mapped_column(default="[]")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class TaskRunRow(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    task_id: Mapped[str] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(index=True)
    trigger: Mapped[str] = mapped_column(default="manual")
    resume_from_run_id: Mapped[str | None] = mapped_column(default=None, index=True)
    result: Mapped[str] = mapped_column(default="{}")
    error: Mapped[str | None] = mapped_column(default=None)
    meta: Mapped[str] = mapped_column(default="{}")
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class ScheduledJobRow(Base):
    __tablename__ = "scheduled_jobs"

    name: Mapped[str] = mapped_column(primary_key=True)
    goal: Mapped[str]
    cron: Mapped[str]
    enabled: Mapped[int] = mapped_column(default=1)
    agent_profile: Mapped[str | None] = mapped_column(default=None, index=True)
    last_run_status: Mapped[str | None] = mapped_column(default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(default=None)
    next_run_at: Mapped[datetime | None] = mapped_column(default=None)
    meta: Mapped[str] = mapped_column(default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class JobRunRow(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    job_name: Mapped[str] = mapped_column(index=True)
    task_id: Mapped[str | None] = mapped_column(default=None, index=True)
    status: Mapped[str] = mapped_column(index=True)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class ToolsetRow(Base):
    __tablename__ = "toolsets"

    name: Mapped[str] = mapped_column(primary_key=True)
    description: Mapped[str | None] = mapped_column(default=None)
    meta: Mapped[str] = mapped_column(default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ToolsetMemberRow(Base):
    __tablename__ = "toolset_members"
    __table_args__ = (Index("idx_toolset_members_tool_name", "tool_name"),)

    toolset_name: Mapped[str] = mapped_column(primary_key=True)
    tool_name: Mapped[str] = mapped_column(primary_key=True)


class TraceTurnRow(Base):
    __tablename__ = "trace_turns"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    conversation_id: Mapped[str | None] = mapped_column(default=None, index=True)
    agent_profile: Mapped[str | None] = mapped_column(default=None)
    task_id: Mapped[str | None] = mapped_column(default=None, index=True)
    task_run_id: Mapped[str | None] = mapped_column(default=None)
    provider_name: Mapped[str]
    model: Mapped[str | None] = mapped_column(default=None)
    prompt_trace_id: Mapped[str | None] = mapped_column(default=None)
    context_trace_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(index=True)
    stop_reason: Mapped[str | None] = mapped_column(default=None)
    error_type: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    cache_read_tokens: Mapped[int | None] = mapped_column(default=None)
    cache_write_tokens: Mapped[int | None] = mapped_column(default=None)
    reasoning_tokens: Mapped[int | None] = mapped_column(default=None)
    cost_usd: Mapped[float | None] = mapped_column(default=None)
    cost_status: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    meta: Mapped[str] = mapped_column(default="{}")


class TraceProviderCallRow(Base):
    __tablename__ = "trace_provider_calls"
    __table_args__ = (Index("idx_trace_provider_calls_turn", "turn_id"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    turn_id: Mapped[str]
    provider_name: Mapped[str]
    model: Mapped[str | None] = mapped_column(default=None)
    log_id: Mapped[str | None] = mapped_column(default=None)
    request_summary: Mapped[str] = mapped_column(default="{}")
    response_summary: Mapped[str] = mapped_column(default="{}")
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    error_type: Mapped[str | None] = mapped_column(default=None)


class TraceToolCallRow(Base):
    __tablename__ = "trace_tool_calls"
    __table_args__ = (
        Index("idx_trace_tool_calls_turn", "turn_id"),
        Index("idx_trace_tool_calls_tool", "tool_name"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    turn_id: Mapped[str]
    provider_call_id: Mapped[str | None] = mapped_column(default=None)
    tool_name: Mapped[str]
    arguments: Mapped[str] = mapped_column(default="{}")
    result_summary: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[str]
    error_message: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class AuxiliaryClientRow(Base):
    """`auxiliary_clients` 表(v19,B3 wave 2):副 model 路由表。

    - `name`:业务面 ID(主键),如 'summarizer' / 'critic_aux'
    - `provider_entry`:指向 `providers.name`(运行时校验,不加 FK)
    - `model`:可空;空时回退到 provider_entry options.model
    - `params`:JSON sampling 默认值;独立预算,避免摘要把主任务 token 预算吃光
    """

    __tablename__ = "auxiliary_clients"

    name: Mapped[str] = mapped_column(primary_key=True)
    provider_entry: Mapped[str]
    model: Mapped[str | None] = mapped_column(default=None)
    params: Mapped[str] = mapped_column(default="{}")  # JSON-serialized dict
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class TraceCheckpointRow(Base):
    __tablename__ = "trace_checkpoints"
    __table_args__ = (Index("idx_trace_checkpoints_turn", "turn_id"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_ulid)
    turn_id: Mapped[str]
    kind: Mapped[str]
    snapshot_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
