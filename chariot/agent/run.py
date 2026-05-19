"""AIAgent — 内核主入口。

职责:
1. 持有 `provider ref(id / slug) → BaseProvider` 实例字典
2. 持有 `name → BaseTool` 实例字典(从 DB tools 表装载)
3. `run_chat(req)` 主入口:路由 + default tools 注入 + 锁包装 + 委托 AgentLoop

接口契约:
- 输入:`ChatRequest`(typed,跟 Claude API 1:1)
- 输出:`AsyncIterator[ChatEvent]`(协议无关,纯流式)
- 错误传播:全部转 `ChatEvent(kind="error", error_type=...)` yield 给 surface
  (CLI / sidecar / Gateway 各自映射成自己的错误形态)

详见 `docs/DESIGN.md` §6.1。

模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, ToolSchema
from chariot.agent.conversation_lock import ConversationLockManager
from chariot.agent.exceptions import ConversationLockTimeout
from chariot.agent.loop import AgentLoop
from chariot.memory.policy import MemoryPolicy
from chariot.models.provider import ProviderEntry
from chariot.providers.contract import normalize_request

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.agent.config import Capabilities
    from chariot.agent.reflection import CriticAgent
    from chariot.audit import AuditHookManager
    from chariot.checkpoints import CheckpointManager
    from chariot.context.compressor import ContextCompressor
    from chariot.context.references import ReferenceExpander
    from chariot.guardrails import GuardrailEngine
    from chariot.guardrails.approval import ApprovalPolicy
    from chariot.models.agent import AgentProfile
    from chariot.providers.base import BaseProvider
    from chariot.services.conversation import ConversationHistoryStore
    from chariot.skills import SkillRegistry
    from chariot.skills.propose_service import SkillProposeService
    from chariot.tools.base import BaseTool
    from chariot.trace import TurnHandle


@dataclass(frozen=True)
class _AgentBinding:
    """已解析的 agent_profile binding 状态(per request)。

    - `profile`:AgentProfile dataclass;None 表示无绑定或 dangling(不存在的 name)
    - `allowed_tools`:`None` = 不做 toolset filter,`frozenset[str]` = 只允许列表里
      的 tool name(空集 = 关闭工具调用)
    """

    agent_profile: AgentProfile | None = None
    allowed_tools: frozenset[str] | None = None


_NO_BINDING: _AgentBinding = _AgentBinding()
_LOG = logging.getLogger("chariot.agent")


class AIAgent:
    """0.6.0 内核入口。每个 surface 进程可有 N 个实例(0.6.5 起 per-session)。

    使用方式::

        # 生产路径(CLI / sidecar / Gateway 通过 AgentRegistry 拿)
        from chariot.agent.registry import AgentRegistry
        agent = await AgentRegistry.reserve(
            session_key="...",
            db_path=Path("~/.chariot/chariot.db"),
            provider_overrides={"claude": {"base_url": X, "api_key": Y}} or None,
        )
        async for event in agent.run_chat(req):
            # 消费 ChatEvent

        # 测试路径(直接注入 mock providers / tools)
        agent = AIAgent(
            providers={"mock": MockProvider.create({})},
            tools={},
            sessionmaker=test_sm,
        )

    0.6.5 起:
    - 撤 `_current` ClassVar 单例 / `current()` / `uninstall()`(改 AgentRegistry per-session)
    - 撤 `patch_provider_options`(S.7.3 临时方案;改 bootstrap 时 provider_overrides 一次性 merge)
    - 加 `bootstrap(db_path, *, provider_overrides=None)` 类方法(替代 0.6.0 的 `from_db`;
      新名表"从零完整装载 = 读 DB + 跑 migrations + 装 N 个 Provider/Tool 实例 + 接 sessionmaker",
      不再用 `from_*` 那种"轻量反序列化"语义)
    """

    def __init__(
        self,
        *,
        providers: dict[str, BaseProvider],
        tools: dict[str, BaseTool],
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        provider_entries: dict[str, ProviderEntry] | None = None,
        context_compressor: ContextCompressor | None = None,
        reference_expander: ReferenceExpander | None = None,
        critic_agent: CriticAgent | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
        audit_hooks: AuditHookManager | None = None,
        capabilities: Capabilities | None = None,
        checkpoint_manager: CheckpointManager | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_propose_service: SkillProposeService | None = None,
    ) -> None:
        from chariot.agent.config import Capabilities as _Capabilities
        from chariot.audit import AuditHookManager as _AuditHookManager
        from chariot.guardrails.approval import ApprovalPolicy as _ApprovalPolicy
        from chariot.trace import TraceWriter

        self._providers = dict(providers)
        self._provider_entries = dict(provider_entries or {})
        if not self._provider_entries:
            self._provider_entries = {
                ref: ProviderEntry(
                    id=ref,
                    slug=ref,
                    name=ref,
                    type=getattr(provider.config, "name", ref),
                    options={},
                    params={},
                )
                for ref, provider in self._providers.items()
            }
        self._tools = dict(tools)
        self._sessionmaker = sessionmaker
        # Phase B1:trace 写入入口。best-effort,失败不阻断主链路;
        # sessionmaker=None 时 writer 自动退化为 no-op。
        self._trace = TraceWriter(sessionmaker)
        # B3 wave 2:可选副 model 压缩器(stateful 长对话用);bootstrap 时若
        # auxiliary_clients 表里有 'summarizer' 行就装上,否则保持 None(noop)。
        self._context_compressor = context_compressor
        # B3 wave 3:可选 @reference 解析器;bootstrap 默认装。
        self._reference_expander = reference_expander
        # B4 wave 1:可选 critic 副 LLM;auxiliary_clients.name='critic' 行装上。
        # 装上不等于自动跑 reflection — wave 3 起 agent_profile.reflection_enabled 才触发。
        self._critic_agent = critic_agent
        # B5 wave 1:可选 guardrail 引擎;bootstrap 默认装 13 内置规则。
        # 不装 = 不拦截(向后兼容 / 测试用)。
        self._guardrail_engine = guardrail_engine
        # B5 wave 1:approval policy(REQUIRE_APPROVAL → 放/不放);wave 3 接 yolo。
        self._approval_policy = approval_policy or _ApprovalPolicy()
        # B5 wave 2:audit hook manager(best-effort 写 audit_events);默认绑
        # sessionmaker;无 sm 时退化 no-op。
        self._audit_hooks = audit_hooks or _AuditHookManager(sessionmaker)
        # B5 wave 3:capability gating(enable_self_mod / yolo)。bootstrap 从 DB
        # 装 enable_self_mod;yolo 由调用方(CLI --yolo / sidecar)传入。
        self._capabilities = capabilities or _Capabilities.default()
        # B5 wave 3:CheckpointManager 三件套 snapshot + rollback;无 sm 退化 None。
        self._checkpoint_manager = checkpoint_manager
        # B6 wave 1:SkillRegistry(builtin YAML + DB skills union);bootstrap 默认
        # 装,测试路径若没传 → None,activator/propose 等下游 None-check 后跳过。
        self._skill_registry = skill_registry
        # B6 wave 3:SkillProposeService(propose_skill tool 走的全链路 orchestrator)。
        # bootstrap 默认装并 attach 到 propose_skill tool;测试路径不传 → propose_skill
        # 执行时返 is_error。
        self._skill_propose_service = skill_propose_service
        self._todo_stores: dict[str | None, Any] = {}
        self._attach_propose_service_if_present()

    # ---- 装载 ----

    @classmethod
    async def bootstrap(
        cls,
        db_path: Path,
        *,
        provider_overrides: dict[str, dict[str, str]] | None = None,
        yolo: bool = False,
    ) -> Self:
        """从零装载 AIAgent —— 开 DB + 跑 migrations + 装 ProviderEntry / ToolEntry
        + 实例化所有 Provider / Tool + 配 sessionmaker,返就绪 AIAgent。

        命名:0.6.5 起改 `bootstrap`(原 `from_db`);新名表"从零引导启动"全套
        动作语义,不再用 `from_*` 那种"轻量反序列化"误导(参考 web framework /
        k8s 通用术语)。

        `provider_overrides`(0.6.5 起):per-session 注入到 entry.options 的 patch
        dict,按 provider id / slug 指定。merge 进 entry.options
        后传给 `ProviderRegistry.build`,Provider 内部从 merged options 算 ClientSpec
        命中或新建 client(共享 ClientCache)。CLI 一次性进程也走这条路径,只是
        session_key 固定 `"process"`。

        首次启动表为空 → 自动 seed:`providers` 插一条 Mock provider,`tools` 插
        4 条 disabled fixture(read_file / list_dir / shell_exec / http_get)。
        seeded 工具默认 disabled,所以新装的 AIAgent 无 tool;mock provider
        已可用。

        典型由 `AgentRegistry.reserve` 调用;直接调也 OK(测试或 single-session
        场景)。
        """
        from chariot.agent.auxiliary_client import AuxiliaryClient
        from chariot.agent.config import Capabilities, ChariotConfig, ToolConfig
        from chariot.context.compressor import ContextCompressor
        from chariot.database.session import init_db
        from chariot.providers.registry import ProviderRegistry
        from chariot.services.auxiliary import AuxiliaryService
        from chariot.services.context import ContextService
        from chariot.services.prompt import PromptService
        from chariot.services.provider import ProviderService
        from chariot.services.tool import ToolService
        from chariot.tools.registry import ToolRegistry

        sm = await init_db(db_path)
        async with sm() as session:
            await ProviderService(session).seed_if_empty()
            await ToolService(session).sync_builtin_tools()
            await PromptService(session).seed_if_empty()
            await ContextService(session).seed_if_empty()
            cfg = await ChariotConfig.from_db(session)
            tool_cfg = await ToolConfig.from_db(session)
            aux_entries = await AuxiliaryService(session).list_entries()
            # B5 wave 3:装 capabilities(从 DB 装 enable_self_mod;yolo 是
            # per-process bootstrap 入参,不读 DB 的 yolo 行)
            capabilities = await Capabilities.from_db(session, yolo=yolo)

        overrides = provider_overrides or {}
        providers: dict[str, BaseProvider] = {}
        provider_entries: dict[str, ProviderEntry] = {}
        for entry in cfg.providers:
            provider = ProviderRegistry.build(
                entry.type,
                {
                    **entry.options,
                    **overrides.get(entry.id, {}),
                    **overrides.get(entry.slug, {}),
                },
            )
            for ref in (entry.id, entry.slug):
                providers[ref] = provider
                provider_entries[ref] = entry
        tools: dict[str, BaseTool] = {}
        for entry in tool_cfg.tools:
            try:
                tools[entry.name] = ToolRegistry.build(entry)
            except Exception:
                if entry.source != "custom":
                    raise
                _LOG.warning("skip invalid custom tool during bootstrap: %s", entry.name, exc_info=True)

        # B3 wave 2:装 ContextCompressor —— auxiliary_clients 表里有 'summarizer'
        # 且其 provider_id 已装时构造,否则保持 None(运行时跳过压缩)。
        compressor: ContextCompressor | None = None
        for aux_entry in aux_entries:
            if aux_entry.name != "summarizer":
                continue
            aux_provider = providers.get(aux_entry.provider_id)
            if aux_provider is None:
                break  # dangling reference,fallback noop
            compressor = ContextCompressor(
                aux_client=AuxiliaryClient(entry=aux_entry, provider=aux_provider),
            )
            break

        # B3 wave 3:装 ReferenceExpander —— cwd 锚到进程当前目录,allowed_domains
        # 继承 http_get tool 的白名单(找不到 http_get 或没配 = 空集 = `@url:` 失败)。
        from chariot.context.references import ReferenceExpander

        expander = ReferenceExpander(
            cwd=Path.cwd(),
            allowed_domains=cls._derive_allowed_domains(tools),
            sessionmaker=sm,
        )

        # B4 wave 1:装 CriticAgent —— auxiliary_clients 表里有 'critic' 行就装上,
        # dangling provider_id / 找不到行 → None(reflection 路径退化 noop)。
        from chariot.agent.reflection import CriticAgent

        critic = CriticAgent.from_auxiliary_clients(aux_entries, providers)

        # B5 wave 1+3:装 GuardrailEngine + ApprovalPolicy,挂 capabilities 让
        # self_modify_chariot 在 enable_self_mod=True 时降级为 REQUIRE_APPROVAL,
        # 让 ApprovalPolicy 在 yolo=True 时放行 REQUIRE_APPROVAL 类。
        from chariot.guardrails import GuardrailEngine
        from chariot.guardrails.approval import ApprovalPolicy

        guardrails = GuardrailEngine.with_defaults(capabilities=capabilities)
        approval = ApprovalPolicy(capabilities=capabilities)

        # B5 wave 3:CheckpointManager 三件套(共享 audit_hooks);db_path.parent
        # 默认是 ~/.chariot/,checkpoints 子目录放 sqlite/tgz 落盘。
        from chariot.audit import AuditHookManager
        from chariot.checkpoints import CheckpointManager

        audit_hooks = AuditHookManager(sm)
        checkpoint_manager = CheckpointManager(
            sessionmaker=sm,
            db_path=db_path,
            checkpoint_dir=db_path.parent / "checkpoints",
            audit_hooks=audit_hooks,
        )

        # B6 wave 1:装 SkillRegistry —— 扫 builtin/*.yaml + DB skills 表 union。
        # 同名 DB 行覆盖 builtin;DB content 坏 YAML 单条 skip 不阻装载。
        from chariot.skills import SkillRegistry

        skill_registry = await SkillRegistry.load(sm)

        # B6 wave 3:装 SkillProposeService —— propose_skill tool 走的全链路 orchestrator;
        # 在 `cls(...)` 内 attach 到 `propose_skill` tool 实例(若已装载)。
        from chariot.skills.propose_service import SkillProposeService

        skill_propose_service = SkillProposeService(
            sessionmaker=sm,
            audit_hooks=audit_hooks,
            checkpoint_manager=checkpoint_manager,
        )

        return cls(
            providers=providers,
            tools=tools,
            sessionmaker=sm,
            provider_entries=provider_entries,
            context_compressor=compressor,
            reference_expander=expander,
            critic_agent=critic,
            guardrail_engine=guardrails,
            approval_policy=approval,
            audit_hooks=audit_hooks,
            capabilities=capabilities,
            checkpoint_manager=checkpoint_manager,
            skill_registry=skill_registry,
            skill_propose_service=skill_propose_service,
        )

    @staticmethod
    def _derive_allowed_domains(tools: dict[str, BaseTool]) -> frozenset[str]:
        """从 `http_get` tool 实例的 `allowed_domains` 字段拿白名单,给 `@url:` 用。
        没装 http_get tool / 没配 = 空集 = `@url:` 一律失败(safe by default)。
        """
        from chariot.tools.builtin.http_get import HttpGetTool

        for tool in tools.values():
            if isinstance(tool, HttpGetTool):
                http_tool: HttpGetTool = tool
                return frozenset(d.lower() for d in http_tool.allowed_domains)
        return frozenset()

    @property
    def providers(self) -> dict[str, BaseProvider]:
        """已装载的 provider 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._providers)

    @property
    def provider_entries(self) -> tuple[ProviderEntry, ...]:
        seen: set[str] = set()
        ordered: list[ProviderEntry] = []
        for entry in self._provider_entries.values():
            if entry.id in seen:
                continue
            seen.add(entry.id)
            ordered.append(entry)
        ordered.sort(key=lambda item: (item.slug, item.id))
        return tuple(ordered)

    def _provider_record_for_ref(self, ref: str) -> ProviderEntry | None:
        return self._provider_entries.get(ref)

    async def resolve_chat_provider_display(self, req: ChatRequest) -> str | None:
        """按运行时真实绑定规则解析本轮 chat 最终生效的 provider 展示名。"""
        _, provider_record, _ = await self._resolve_effective_provider(req)
        return provider_record.name if provider_record is not None else None

    @property
    def tools(self) -> dict[str, BaseTool]:
        """已装载的 tool 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._tools)

    def _todo_store_for(self, conversation_id: str | None) -> Any:
        from chariot.tools.builtin.todo import _TodoStore

        store = self._todo_stores.get(conversation_id)
        if store is None:
            store = _TodoStore()
            self._todo_stores[conversation_id] = store
        return store

    @property
    def critic_agent(self) -> CriticAgent | None:
        """已装载的 CriticAgent(B4 wave 1)。未装载 = auxiliary_clients 里无
        `name='critic'` 行或 provider entry dangling;调用方需 None-check。"""
        return self._critic_agent

    @property
    def guardrail_engine(self) -> GuardrailEngine | None:
        """已装载的 GuardrailEngine(B5 wave 1)。bootstrap 默认装 13 内置规则。"""
        return self._guardrail_engine

    @property
    def audit_hooks(self) -> AuditHookManager:
        """已装载的 AuditHookManager(B5 wave 2)。bootstrap 默认绑 sessionmaker;
        测试路径若没传 sessionmaker 也没传 audit_hooks → disabled no-op manager。"""
        return self._audit_hooks

    @property
    def capabilities(self) -> Capabilities:
        """已装载的 capability 集合(B5 wave 3)。bootstrap 从 DB 装 enable_self_mod,
        yolo 由 bootstrap 入参传入。"""
        return self._capabilities

    @property
    def checkpoint_manager(self) -> CheckpointManager | None:
        """已装载的 CheckpointManager(B5 wave 3)。bootstrap 默认装;测试路径
        若没传 sessionmaker 也没传 checkpoint_manager → None。"""
        return self._checkpoint_manager

    @property
    def skill_registry(self) -> SkillRegistry | None:
        """已装载的 SkillRegistry(B6 wave 1)。bootstrap 默认装 builtin + DB 行 union;
        测试路径若没传 → None,activator/propose 等下游需 None-check。"""
        return self._skill_registry

    @property
    def skill_propose_service(self) -> SkillProposeService | None:
        """已装载的 SkillProposeService(B6 wave 3)。bootstrap 默认装;测试路径
        不传 → None,propose_skill tool 执行时返 is_error。"""
        return self._skill_propose_service

    def _attach_propose_service_if_present(self) -> None:
        """`propose_skill` tool 实例化时不知 service;bootstrap 在 __init__ 末段把
        service 注入到 tool 上(若已装载)。

        放在 `__init__` 内是为了"调用方直接 `AIAgent(...)` 测试"路径也能 attach 上
        (而不仅是 `AIAgent.bootstrap` 路径)。
        """
        if self._skill_propose_service is None:
            return
        from chariot.tools.builtin.propose_skill import ProposeSkillTool

        for tool in self._tools.values():
            if isinstance(tool, ProposeSkillTool):
                propose_tool: ProposeSkillTool = tool
                propose_tool.attach_service(self._skill_propose_service)

    # ---- 主入口 ----

    async def run_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 chat,yield ChatEvent 流(详见 DESIGN §6.1 / §6.4)。

        路由:按 `req.provider_ref`(provider ref)找 Provider 实例;缺失 →
        yield error event 退出。`req.agent_profile` 非空时先解析 binding:
        - `profile.provider_id` 覆盖 `req.provider_ref`
        - `profile.prompt_id` 由 `_prepare_request` 拣对应 bundle
        - `profile.toolset_id` 由 `_inject_default_tools` 做 toolset filter
        dangling reference 走 fallback 不阻断。

        default tools 注入:`req.tools is None` → 挂所有装载 tool 的 schema。
        stateful(`req.conversation_id` 非空)→ 在 conversation lock 内开 session 跑;
        stateless → 直接跑 AgentLoop 不开 session。

        Phase B1:整段包在 trace turn 内,begin_turn / finalize 由本方法收口;
        AgentLoop 内部的 provider call / tool call 也通过 turn handle 写入。

        B4 wave 2:`req.reflection_enabled=True` 且 critic 已装载时,委托给
        `_run_chat_reflective` 反思-重试编排;否则走单轮 `_run_chat_once`。
        """
        # B4 wave 3:agent_profile.reflection_* 透传到 ChatRequest(profile 字段比
        # request 字段优先,只在 req 显式 reflection_enabled=False 才不覆盖)。
        req = await self._apply_profile_reflection(req)
        if req.reflection_enabled and self._critic_agent is not None and req.reflection_max_retries > 0:
            async for ev in self._run_chat_reflective(req):
                yield ev
            return
        async for ev in self._run_chat_once(req):
            yield ev

    async def _apply_profile_reflection(self, req: ChatRequest) -> ChatRequest:
        """req.agent_profile.reflection_enabled=True → 覆盖 req 的 reflection_*。

        逻辑:
        - 解析 agent_profile;若 profile.reflection_enabled=True 且 req 还没显式开
          → 透传 profile 的 reflection_enabled + reflection_max_retries
        - profile 不存在 / 关 reflection → req 字段保持(可能调用方手动开了)
        """
        if req.agent_profile is None or req.reflection_enabled:
            return req  # 已显式开,不覆盖
        binding = await self._resolve_binding(req)
        profile = binding.agent_profile
        if profile is None or not profile.reflection_enabled:
            return req
        return dataclasses.replace(
            req,
            reflection_enabled=True,
            reflection_max_retries=profile.reflection_max_retries,
        )

    async def _run_chat_once(
        self,
        req: ChatRequest,
        *,
        turn_meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """单轮 chat(原 run_chat 主体)。`turn_meta` 写进 trace_turns.meta
        (B4 wave 2 用,记 reflection iteration 入口信息)。"""
        from chariot.models.trace import TurnStatus

        req, provider_record, binding = await self._resolve_effective_provider(req)

        if req.provider_ref is None:
            yield ChatEvent.error_event(
                error_type="unknown_provider",
                error_message="provider 未指定:选择 agent 或显式指定 provider",
            )
            return

        provider = self._providers.get(req.provider_ref)
        if provider is None or provider_record is None:
            yield ChatEvent.error_event(
                error_type="unknown_provider",
                error_message=(f"unknown provider {req.provider_ref!r}; known: {sorted(self._providers.keys())}"),
            )
            return

        # Phase B1:begin turn(provider 解析后,branch 之前);失败降级 no-op
        turn = await self._trace.begin_turn(
            provider_id=getattr(provider_record, "id", None),
            provider_snapshot=getattr(provider_record, "name", req.provider_ref),
            conversation_id=req.conversation_id,
            agent_profile=binding.agent_profile.name if binding.agent_profile is not None else None,
            model=provider.config.model,
            meta=turn_meta,
        )
        last_usage: dict[str, Any] | None = None
        last_stop_reason: str | None = None
        last_error: ChatEvent | None = None
        was_cancelled = False

        try:
            if req.is_stateful():
                inner = self._run_stateful_chat(req, provider, binding, turn)
            else:
                inner = self._run_stateless_chat(req, provider, binding, turn)
            async for event in inner:
                if event.kind == "message_delta" and event.delta:
                    sr = event.delta.get("stop_reason")
                    if isinstance(sr, str):
                        last_stop_reason = sr
                if event.usage:
                    last_usage = event.usage
                if event.kind == "error":
                    last_error = event
                yield event
        except asyncio.CancelledError:
            was_cancelled = True
            raise
        finally:
            status = (
                TurnStatus.CANCELLED
                if was_cancelled
                else TurnStatus.FAILED
                if last_error is not None
                else TurnStatus.COMPLETED
            )
            await turn.finalize(
                status=status,
                stop_reason=last_stop_reason,
                error_type=(
                    "cancelled" if was_cancelled else last_error.error_type if last_error is not None else None
                ),
                error_message=(
                    "chat cancelled by user"
                    if was_cancelled
                    else last_error.error_message
                    if last_error is not None
                    else None
                ),
                input_tokens=(last_usage or {}).get("input_tokens"),
                output_tokens=(last_usage or {}).get("output_tokens"),
                cache_read_tokens=(last_usage or {}).get("cache_read_input_tokens"),
                cache_write_tokens=(last_usage or {}).get("cache_creation_input_tokens"),
            )

    async def _run_chat_reflective(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """B4 wave 2 反思-重试编排。

        循环:跑 `_run_chat_once` → buffer accumulate → `ReflectionLoop.step`
        → 若 retry,把 [REFLECTION] 注入 messages 跑下一轮;否则结束。

        每一轮 retry 跑一个独立 trace turn;turn.meta 注入 reflection iteration
        入口信息(前一轮的 verdict / trigger / reason),给 trace UI 显示用。
        """
        from chariot.agent.reflection import AssistantBuffer, ReflectionLoop

        assert self._critic_agent is not None  # run_chat 已 gate
        reflection = ReflectionLoop(
            critic=self._critic_agent,
            max_retries=req.reflection_max_retries,
        )
        current_req = req
        entry_meta: dict[str, Any] | None = None

        while True:
            buffer = AssistantBuffer()
            async for ev in self._run_chat_once(current_req, turn_meta=entry_meta):
                buffer.accept(ev)
                yield ev
            step = await reflection.step(buffer=buffer, original_req=current_req)
            if step is None or not step.should_retry:
                return
            entry_meta = {
                "reflection_iteration": step.record.iteration,
                "reflection_trigger": step.record.trigger,
                "reflection_previous_verdict": step.record.verdict,
                "reflection_previous_reason": step.record.reason,
            }
            assert step.revised_req is not None
            current_req = step.revised_req

    async def _resolve_binding(self, req: ChatRequest) -> _AgentBinding:
        """req.agent_profile 非空 → 加载 AgentProfile + 解析 toolset 成员;
        dangling reference 或 no sessionmaker 走 fallback(返 `_NO_BINDING`)。

        0.8.8+ 行为变更:没有配置 toolset 或 dangling toolset → allowed_tools=None,
        在 _inject_default_tools 阶段会关闭工具调用(不挂载任何工具)。
        工具调用必须通过 agent_profile.toolset_id 显式配置。
        """
        if req.agent_profile is None or self._sessionmaker is None:
            return _NO_BINDING
        from chariot.services.agent import AgentService
        from chariot.services.toolset import ToolsetService

        async with self._sessionmaker() as session:
            profile = await AgentService(session).get_agent_by_id(req.agent_profile)
            if profile is None:
                return _NO_BINDING
            if profile.toolset_id is None:
                return _AgentBinding(agent_profile=profile, allowed_tools=None)
            toolset = await ToolsetService(session).get_entry(profile.toolset_id)
            if toolset is None:
                # toolset id 引用不存在 → fallback 到空工具(不挂载)
                return _AgentBinding(agent_profile=profile, allowed_tools=None)
            return _AgentBinding(agent_profile=profile, allowed_tools=frozenset(toolset.members))

    async def _resolve_effective_provider(
        self,
        req: ChatRequest,
    ) -> tuple[ChatRequest, ProviderEntry | None, _AgentBinding]:
        """按 agent_profile 覆盖规则解析最终生效的 provider。"""
        binding = await self._resolve_binding(req)
        resolved_req = req
        if binding.agent_profile is not None and binding.agent_profile.provider_id is not None:
            resolved_req = dataclasses.replace(req, provider_ref=binding.agent_profile.provider_id)
        provider_record = None
        if resolved_req.provider_ref is not None:
            provider_record = self._provider_record_for_ref(resolved_req.provider_ref)
        return resolved_req, provider_record, binding

    async def _run_stateless_chat(
        self,
        req: ChatRequest,
        provider: BaseProvider,
        binding: _AgentBinding = _NO_BINDING,
        turn: TurnHandle | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """无 conversation_id:直接跑 AgentLoop,不锁不持久化。"""
        provider_record = self._provider_record_for_ref(req.provider_ref or "")
        if self._sessionmaker is not None:
            async with self._sessionmaker() as session:
                memory_policy = MemoryPolicy()
                memory_entries = await self._load_memory_entries(session, req, provider_record, memory_policy)
                req = await self._prepare_request(req, provider, memory_entries, memory_policy, binding)
                req = await self._maybe_expand_references(req)
                prompt_trace = await self._record_prompt_trace(
                    session,
                    req,
                    provider_record,
                    provider,
                    memory_entries,
                    memory_policy,
                )
                if turn is not None:
                    await turn.update_links(prompt_trace_id=prompt_trace.id)
        else:
            req = self._normalize_request(req, provider, binding)
            req = await self._maybe_expand_references(req)
        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            message_store=None,
            conversation_id=None,
            turn=turn,
            guardrail_engine=self._guardrail_engine,
            approval_policy=self._approval_policy,
            audit_hooks=self._audit_hooks,
            todo_store=self._todo_store_for(None),
        )
        last_event_kind = None
        last_error_event: ChatEvent | None = None
        async for event in loop.stream_chat(req):
            last_event_kind = event.kind
            if event.kind == "error":
                last_error_event = event
            yield event
        if self._sessionmaker is not None and last_event_kind == "stream_done":
            async with self._sessionmaker() as session:
                await self._capture_memory(
                    session,
                    req=req,
                    provider_record=provider_record,
                    memory_policy=memory_policy,
                    prompt_trace_id=prompt_trace.id,
                )
        elif self._sessionmaker is not None and last_error_event is not None:
            async with self._sessionmaker() as session:
                await self._capture_error_memory(
                    session,
                    req=req,
                    provider_record=provider_record,
                    memory_policy=memory_policy,
                    error_event=last_error_event,
                    prompt_trace_id=prompt_trace.id,
                )

    async def _run_stateful_chat(
        self,
        req: ChatRequest,
        provider: BaseProvider,
        binding: _AgentBinding = _NO_BINDING,
        turn: TurnHandle | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """有 conversation_id:开 session + 进 conversation lock + load history + AgentLoop。

        约定:`req.messages` 是**新增的消息**(通常 1 条 user message);
        server 端 load 历史 prepend。stateful 调用方不发完整对话历史。
        """
        if self._sessionmaker is None:
            yield ChatEvent.error_event(
                error_type="no_sessionmaker",
                error_message="AIAgent 未装载 sessionmaker;stateful 模式需 bootstrap 装载",
            )
            return

        conversation_id = req.conversation_id
        assert conversation_id is not None  # is_stateful() 保证

        async with self._sessionmaker() as session:
            try:
                async with ConversationLockManager.acquire(conversation_id, db_session=session):
                    async for event in self._run_stateful_turn(req, conversation_id, provider, session, binding, turn):
                        yield event
            except ConversationLockTimeout as e:
                yield ChatEvent.error_event(
                    error_type=f"conversation_busy_{e.layer}",
                    error_message=str(e),
                )

    async def _run_stateful_turn(
        self,
        req: ChatRequest,
        conversation_id: str,
        provider: BaseProvider,
        session: AsyncSession,
        binding: _AgentBinding = _NO_BINDING,
        turn: TurnHandle | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """conversation lock 内的实际工作:ensure conversation / persist new user / load history /
        跑 AgentLoop。"""
        from chariot.context.composer import ContextComposer
        from chariot.services.context import ContextService
        from chariot.services.conversation import ConversationService

        conversation_service = ConversationService(session)
        context_service = ContextService(session)
        provider_record = self._provider_record_for_ref(req.provider_ref or "")
        await conversation_service.ensure_exists(conversation_id)
        await self._persist_new_user_messages(conversation_service, conversation_id, req)
        history = await conversation_service.load_history_as_messages(conversation_id)
        memory_policy = MemoryPolicy()
        memory_entries = await self._load_memory_entries(session, req, provider_record, memory_policy)
        context_bundle_ref = binding.agent_profile.context_id if binding.agent_profile is not None else None
        context_bundle = await context_service.resolve_bundle(bundle_name=context_bundle_ref)
        context_version = (
            await context_service.get_active_version(context_bundle.name) if context_bundle is not None else None
        )
        context_snapshot = await context_service.record_snapshot(
            ContextComposer.build_snapshot(
                req,
                provider_id=getattr(provider_record, "id", None),
                provider_snapshot=getattr(provider_record, "name", req.provider_ref or "unknown"),
                model=provider.config.model,
                history=[{"role": msg.role, "content": msg.content} for msg in history],
                memory_entries=memory_entries,
                memory_policy=memory_policy.describe(),
                context_policy=context_version.spec if context_version is not None else None,
                provider_capabilities=dataclasses.asdict(provider.capabilities),
            )
        )
        full_req = dataclasses.replace(req, messages=history)
        full_req = await self._prepare_request(full_req, provider, memory_entries, memory_policy, binding)
        full_req = await self._maybe_expand_references(full_req)
        full_req = await self._maybe_compress_context(full_req, provider, turn)
        prompt_trace = await self._record_prompt_trace(
            session,
            full_req,
            provider_record,
            provider,
            memory_entries,
            memory_policy,
        )
        await context_service.record_trace(
            context_snapshot.id,
            prompt_trace_id=prompt_trace.id,
            policy_name=context_version.version if context_version is not None else "default_context_policy",
            bundle_id=context_bundle.id if context_bundle is not None else None,
            version_id=context_version.id if context_version is not None else None,
        )
        if turn is not None:
            await turn.update_links(
                prompt_trace_id=prompt_trace.id,
                context_trace_id=context_snapshot.id,
            )

        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            message_store=conversation_service,
            conversation_id=conversation_id,
            turn=turn,
            guardrail_engine=self._guardrail_engine,
            approval_policy=self._approval_policy,
            audit_hooks=self._audit_hooks,
            todo_store=self._todo_store_for(conversation_id),
            agent_profile=binding.agent_profile.id if binding.agent_profile is not None else None,
            provider_snapshot=req.provider_ref,
        )
        last_event_kind = None
        last_error_event: ChatEvent | None = None
        async for event in loop.stream_chat(full_req):
            last_event_kind = event.kind
            if event.kind == "error":
                last_error_event = event
            yield event
        if last_event_kind == "stream_done":
            await self._capture_memory(
                session,
                req=req,
                provider_record=provider_record,
                memory_policy=memory_policy,
                prompt_trace_id=prompt_trace.id,
                context_trace_id=context_snapshot.id,
            )
        elif last_error_event is not None:
            await self._capture_error_memory(
                session,
                req=req,
                provider_record=provider_record,
                memory_policy=memory_policy,
                error_event=last_error_event,
                prompt_trace_id=prompt_trace.id,
                context_trace_id=context_snapshot.id,
            )

    @staticmethod
    async def _persist_new_user_messages(
        repo: ConversationHistoryStore, conversation_id: str, req: ChatRequest
    ) -> None:
        """req.messages 里新增的 user 消息(末尾若干条 role='user')落库。

        简化策略:把 req.messages 整体当"新增"持久化(假设 client 在 stateful
        模式下只发新增消息;若发了完整 history,下一轮 load 会重复,这里不防御
        —— 由 client 契约保证)。
        """
        for msg in req.messages:
            if msg.role != "user":
                continue
            content = msg.content if isinstance(msg.content, list) else [{"type": "text", "text": msg.content}]
            await repo.append_user_message(conversation_id, content)

    # ---- B3 wave 3: @reference 解析 ----

    async def _maybe_expand_references(self, req: ChatRequest) -> ChatRequest:
        """跑 ReferenceExpander.expand;无 expander 或无 @reference → 原样返。

        策略:expansion 只影响送给 provider 的 in-memory req.messages;**不改
        persisted messages**(stateful 模式下,持久化的是用户原文 `@file:...`,
        让 conversation history 保留输入的可读性)。
        """
        if self._reference_expander is None:
            return req
        try:
            new_messages = await self._reference_expander.expand(req.messages)
        except Exception:  # pragma: no cover - best-effort 容错
            return req
        if new_messages is req.messages:
            return req
        return dataclasses.replace(req, messages=new_messages)

    # ---- B3 wave 2: 长对话压缩 ----

    async def _maybe_compress_context(
        self,
        req: ChatRequest,
        provider: BaseProvider,
        turn: TurnHandle | None,
    ) -> ChatRequest:
        """跑 ContextCompressor.maybe_compress;压缩了就把 result 写到 turn.meta。

        无 compressor / 未触发 → 直接返原 req,trace meta 不动。
        compressor 抛非预期异常 → best-effort 吞掉,返原 req(主链路不阻断)。
        """
        if self._context_compressor is None:
            return req
        context_length = self._provider_context_length(provider)
        try:
            new_req, result = await self._context_compressor.maybe_compress(req, context_length=context_length)
        except Exception:  # pragma: no cover - best-effort 容错
            return req
        if result.compressed and turn is not None:
            await turn.merge_meta(
                {
                    "context_compressed": True,
                    "context_compression": {
                        "strategy": result.strategy,
                        "dropped_turns": result.dropped_turns,
                        "prompt_tokens_estimate": result.prompt_tokens_estimate,
                        "context_length": context_length,
                    },
                }
            )
        return new_req

    @staticmethod
    def _provider_context_length(provider: BaseProvider) -> int:
        """从 provider config / capabilities 读 context_length,缺省 8192。

        约定:某些 provider 子类可在 config 上挂 `context_length` 字段(动态读)。
        Mock / Anthropic 没挂时回退默认。
        """
        ctx = getattr(provider.config, "context_length", None)
        if isinstance(ctx, int) and ctx > 0:
            return ctx
        return 8192

    # ---- default tools 注入 ----

    def _normalize_request(
        self,
        req: ChatRequest,
        provider: BaseProvider,
        binding: _AgentBinding = _NO_BINDING,
    ) -> ChatRequest:
        """Apply agent-side defaults, then trim fields unsupported by the provider."""

        req_with_tools = self._inject_default_tools(req, binding)
        return normalize_request(req_with_tools, provider.capabilities)

    def _inject_default_tools(self, req: ChatRequest, binding: _AgentBinding = _NO_BINDING) -> ChatRequest:
        """req.tools 是 None → 按 binding 挂载 tool schema;
        req.tools 是 [] → 关闭工具调用(透传);
        req.tools 是 list → 用调用方指定的(透传)。

        0.8.8+ 行为变更:没有配置 toolset(allowed_tools is None) → 不挂载任何工具,
        工具调用必须显式通过 agent_profile.toolset_id 配置。
        """
        if req.tools is not None:
            return req  # 调用方已指定(含 [] 关闭工具)
        if not self._tools:
            return req  # 没装载 tool

        # 新行为:没有配置 toolset → 不挂载任何工具
        if binding.allowed_tools is None:
            return dataclasses.replace(req, tools=[])

        # 配置了 toolset → 按 toolset 过滤
        tools = {name: tool for name, tool in self._tools.items() if name in binding.allowed_tools}
        schemas: list[ToolSchema] = [self._tool_schema(tool) for tool in tools.values()]
        return dataclasses.replace(req, tools=schemas)

    @staticmethod
    def _tool_schema(tool: BaseTool) -> ToolSchema:
        raw = tool.schema()
        return ToolSchema(
            name=raw.get("name", tool.name),
            description=raw.get("description", ""),
            input_schema=raw.get("input_schema", {}),
        )

    async def _prepare_request(
        self,
        req: ChatRequest,
        provider: BaseProvider,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: MemoryPolicy | None = None,
        binding: _AgentBinding = _NO_BINDING,
    ) -> ChatRequest:
        """Compose prompt bundle into `system`, then normalize request fields.

        binding.agent_profile.prompt_id 非空 → 按稳定 id 取指定 bundle;
        dangling 时回退到 active bundle。

        B6 wave 2:normalize 之后跑 SkillActivator —— 在 `_inject_default_tools`
        已把 toolset filter 走完之后,skill.tool_filter 再过一次(取交集 - forbidden);
        `<skill>` 块拼到 system 末尾(让 skill 是"最后一层提示")。
        """
        composed = req
        if self._sessionmaker is not None:
            from chariot.prompt.composer import PromptComposer
            from chariot.services.prompt import PromptService

            async with self._sessionmaker() as session:
                prompt_service = PromptService(session)
                bundle_ref = binding.agent_profile.prompt_id if binding.agent_profile is not None else None
                bundle = await prompt_service.resolve_bundle(bundle_name=bundle_ref)
                if bundle is not None:
                    existing_system = req.system if isinstance(req.system, str) else None
                    system = PromptComposer.render_layers_text(
                        bundle.layers,
                        existing_system=existing_system,
                        memory_entries=memory_entries,
                        memory_policy=memory_policy.describe() if memory_policy is not None else None,
                    )
                    composed = dataclasses.replace(req, system=system)
        normalized = self._normalize_request(composed, provider, binding)
        return await self._maybe_activate_skill(normalized, binding)

    async def _maybe_activate_skill(self, req: ChatRequest, binding: _AgentBinding) -> ChatRequest:
        """B6 wave 2:按 `req.skill` / `profile.default_skill` 决定是否注入 skill。

        优先级:
        - `req.skill == ""` → 显式清空(覆盖 profile.default_skill,不激活)
        - `req.skill` 非空 → 用它
        - `req.skill is None` + `profile.default_skill` 非空 → 用 profile 字段
        - 都 None → 不激活

        dangling reference / disabled skill / registry 未装 → 静默 skip(跟
        prompt_id dangling 同款 fallback,不阻断 chat)。
        """
        if self._skill_registry is None:
            return req
        if req.skill is not None and req.skill == "":
            return req
        skill_name = req.skill
        if skill_name is None and binding.agent_profile is not None:
            skill_name = binding.agent_profile.default_skill
        if not skill_name:
            return req
        skill = self._skill_registry.get(skill_name)
        if skill is None or not skill.enabled:
            return req
        from chariot.skills import SkillActivator

        activated = SkillActivator.activate(req, skill)
        # B6 wave 4:每次 activate 写 audit_events.skill_activate(给 Curator 喂数据)
        await self._audit_hooks.record_skill_activate(
            skill_name=skill.name,
            source=skill.source,
            conversation_id=req.conversation_id,
            agent_profile=binding.agent_profile.name if binding.agent_profile is not None else None,
        )
        return activated

    async def _load_memory_entries(
        self,
        session: AsyncSession,
        req: ChatRequest,
        provider: ProviderEntry | None,
        policy: MemoryPolicy,
    ) -> list[dict[str, Any]] | None:
        from chariot.services.memory import MemoryService

        return await MemoryService(session).list_relevant_entry_payloads(
            conversation_id=req.conversation_id,
            provider_snapshot=getattr(provider, "name", None),
            limit=policy.max_items,
            policy=policy,
        )

    @staticmethod
    async def _record_prompt_trace(
        session: AsyncSession,
        req: ChatRequest,
        provider_record: ProviderEntry | None,
        provider: BaseProvider,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: MemoryPolicy | None = None,
    ) -> Any:
        from chariot.services.prompt import PromptService

        return await PromptService(session).record_trace(
            req,
            provider_id=getattr(provider_record, "id", None),
            provider_snapshot=getattr(provider_record, "name", provider.config.name),
            model=provider.config.model,
            memory_entries=memory_entries,
            memory_policy=memory_policy.describe() if memory_policy is not None else None,
        )

    async def _capture_memory(
        self,
        session: AsyncSession,
        *,
        req: ChatRequest,
        provider_record: ProviderEntry | None,
        memory_policy: MemoryPolicy,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> None:
        from chariot.services.memory import MemoryService

        await MemoryService(session).capture_turn(
            req=req,
            provider_snapshot=str(getattr(provider_record, "name", req.provider_ref or "unknown")),
            policy=memory_policy,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
            audit_hooks=self._audit_hooks,
        )

    async def _capture_error_memory(
        self,
        session: AsyncSession,
        *,
        req: ChatRequest,
        provider_record: ProviderEntry | None,
        memory_policy: MemoryPolicy,
        error_event: ChatEvent,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> None:
        from chariot.services.memory import MemoryService

        await MemoryService(session).capture_error(
            conversation_id=req.conversation_id,
            provider_snapshot=str(getattr(provider_record, "name", req.provider_ref or "unknown")),
            error_event=error_event,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
            audit_hooks=self._audit_hooks,
        )
