"""AIAgent — 内核主入口。

职责:
1. 持有 `name → BaseProvider` 实例字典(从 DB providers 表装载)
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

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message, ToolSchema
from chariot.agent.conversation_lock import ConversationLockManager
from chariot.agent.exceptions import ConversationLockTimeout
from chariot.agent.loop import AgentLoop
from chariot.agent.provider_contract import normalize_request
from chariot.memory.capture import MemoryCaptureService
from chariot.memory.policy import MemoryPolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.providers.base import BaseProvider
    from chariot.repos.conversation_repo import ConversationRepo
    from chariot.tools.base import BaseTool


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
    ) -> None:
        self._providers = dict(providers)
        self._tools = dict(tools)
        self._sessionmaker = sessionmaker

    # ---- 装载 ----

    @classmethod
    async def bootstrap(
        cls,
        db_path: Path,
        *,
        provider_overrides: dict[str, dict[str, str]] | None = None,
    ) -> Self:
        """从零装载 AIAgent —— 开 DB + 跑 migrations + 装 ProviderEntry / ToolEntry
        + 实例化所有 Provider / Tool + 配 sessionmaker,返就绪 AIAgent。

        命名:0.6.5 起改 `bootstrap`(原 `from_db`);新名表"从零引导启动"全套
        动作语义,不再用 `from_*` 那种"轻量反序列化"误导(参考 web framework /
        k8s 通用术语)。

        `provider_overrides`(0.6.5 起):per-session 注入到 entry.options 的 patch
        dict,形如 `{"claude": {"base_url": X, "api_key": Y}}`。merge 进 entry.options
        后传给 `ProviderRegistry.build`,Provider 内部从 merged options 算 ClientSpec
        命中或新建 client(共享 ClientCache)。CLI 一次性进程也走这条路径,只是
        session_key 固定 `"process"`。

        首次启动表为空 → 自动 seed:`providers` 插一条 mock entry,`tools` 插
        4 条 disabled fixture(read_file / list_dir / shell_exec / http_get)。
        seeded 工具默认 disabled,所以新装的 AIAgent 无 tool;mock provider
        已可用。

        典型由 `AgentRegistry.reserve` 调用;直接调也 OK(测试或 single-session
        场景)。
        """
        from chariot.agent.config import ChariotConfig, ToolConfig
        from chariot.database.session import init_db
        from chariot.providers.registry import ProviderRegistry
        from chariot.repos.prompt_repo import PromptRepo
        from chariot.repos.provider_repo import ProviderRepo
        from chariot.repos.tool_repo import ToolRepo
        from chariot.tools.registry import ToolRegistry

        sm = await init_db(db_path)
        async with sm() as session:
            await ProviderRepo(session).seed_if_empty()
            await ToolRepo(session).seed_if_empty()
            await PromptRepo(session).seed_if_empty()
            cfg = await ChariotConfig.from_db(session)
            tool_cfg = await ToolConfig.from_db(session)

        overrides = provider_overrides or {}
        providers: dict[str, BaseProvider] = {
            entry.name: ProviderRegistry.build(
                entry.type,
                {**entry.options, **overrides.get(entry.name, {})},
            )
            for entry in cfg.providers
        }
        tools: dict[str, BaseTool] = {entry.name: ToolRegistry.build(entry) for entry in tool_cfg.tools}

        return cls(providers=providers, tools=tools, sessionmaker=sm)

    # ---- 资源访问(供 surface 直调 repo) ----

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]:
        """暴露 sessionmaker 给 surface(CLI / sidecar)直调 repo;未装载抛 RuntimeError。

        典型用法::

            async with agent.session_maker() as session:
                repo = ConversationRepo(session)
                entries = await repo.list_entries()
        """
        if self._sessionmaker is None:
            raise RuntimeError("AIAgent 未装载 sessionmaker;先调 AIAgent.bootstrap(db_path)")
        return self._sessionmaker

    @property
    def providers(self) -> dict[str, BaseProvider]:
        """已装载的 provider 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._providers)

    @property
    def tools(self) -> dict[str, BaseTool]:
        """已装载的 tool 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._tools)

    # ---- 主入口 ----

    async def run_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 chat,yield ChatEvent 流(详见 DESIGN §6.1 / §6.4)。

        路由:按 `req.provider_name`(entry name)找 Provider 实例;缺失 →
        yield error event 退出。
        default tools 注入:`req.tools is None` → 挂所有装载 tool 的 schema。
        stateful(`req.conversation_id` 非空)→ 在 conversation lock 内开 session 跑;
        stateless → 直接跑 AgentLoop 不开 session。
        """
        provider = self._providers.get(req.provider_name)
        if provider is None:
            yield ChatEvent.error_event(
                error_type="unknown_provider",
                error_message=(
                    f"unknown provider entry {req.provider_name!r}; known: {sorted(self._providers.keys())}"
                ),
            )
            return

        if req.is_stateful():
            async for event in self._run_stateful_chat(req, provider):
                yield event
        else:
            async for event in self._run_stateless_chat(req, provider):
                yield event

    async def _run_stateless_chat(self, req: ChatRequest, provider: BaseProvider) -> AsyncIterator[ChatEvent]:
        """无 conversation_id:直接跑 AgentLoop,不锁不持久化。"""
        if self._sessionmaker is not None:
            async with self._sessionmaker() as session:
                memory_policy = MemoryPolicy()
                memory_entries = await self._load_memory_entries(session, req, provider, memory_policy)
                req = await self._prepare_request(req, provider, memory_entries, memory_policy)
                prompt_trace = await self._record_prompt_trace(
                    session,
                    req,
                    provider,
                    memory_entries,
                    memory_policy,
                )
        else:
            req = self._normalize_request(req, provider)
        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            repo=None,
            conversation_id=None,
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
                    provider=provider,
                    memory_policy=memory_policy,
                    prompt_trace_id=prompt_trace.id,
                )
        elif self._sessionmaker is not None and last_error_event is not None:
            async with self._sessionmaker() as session:
                await self._capture_error_memory(
                    session,
                    req=req,
                    provider=provider,
                    memory_policy=memory_policy,
                    error_event=last_error_event,
                    prompt_trace_id=prompt_trace.id,
                )

    async def _run_stateful_chat(self, req: ChatRequest, provider: BaseProvider) -> AsyncIterator[ChatEvent]:
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
                    async for event in self._run_stateful_turn(req, conversation_id, provider, session):
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
    ) -> AsyncIterator[ChatEvent]:
        """conversation lock 内的实际工作:ensure conversation / persist new user / load history /
        跑 AgentLoop。"""
        from chariot.context.composer import ContextComposer
        from chariot.repos.context_repo import ContextRepo
        from chariot.repos.conversation_repo import ConversationRepo

        repo = ConversationRepo(session)
        await repo.ensure_exists(conversation_id)
        await self._persist_new_user_messages(repo, conversation_id, req)
        history = await self._load_history_as_messages(repo, conversation_id)
        context_repo = ContextRepo(session)
        memory_policy = MemoryPolicy()
        memory_entries = await self._load_memory_entries(session, req, provider, memory_policy)
        context_snapshot = await context_repo.record_snapshot(
            ContextComposer.build_snapshot(
                req,
                provider_name=provider.config.name,
                model=provider.config.model,
                history=[{"role": msg.role, "content": msg.content} for msg in history],
                memory_entries=memory_entries,
                memory_policy=memory_policy.describe(),
                provider_capabilities=dataclasses.asdict(provider.capabilities),
            )
        )
        full_req = dataclasses.replace(req, messages=history)
        full_req = await self._prepare_request(full_req, provider, memory_entries, memory_policy)
        prompt_trace = await self._record_prompt_trace(
            session,
            full_req,
            provider,
            memory_entries,
            memory_policy,
        )
        await context_repo.record_trace(
            context_snapshot.id,
            prompt_trace_id=prompt_trace.id,
        )

        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            repo=repo,
            conversation_id=conversation_id,
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
                provider=provider,
                memory_policy=memory_policy,
                prompt_trace_id=prompt_trace.id,
                context_trace_id=context_snapshot.id,
            )
        elif last_error_event is not None:
            await self._capture_error_memory(
                session,
                req=req,
                provider=provider,
                memory_policy=memory_policy,
                error_event=last_error_event,
                prompt_trace_id=prompt_trace.id,
                context_trace_id=context_snapshot.id,
            )

    @staticmethod
    async def _persist_new_user_messages(repo: ConversationRepo, conversation_id: str, req: ChatRequest) -> None:
        """req.messages 里新增的 user 消息(末尾若干条 role='user')落库。

        简化策略:把 req.messages 整体当"新增"持久化(假设 client 在 stateful
        模式下只发新增消息;若发了完整 history,下一轮 load 会重复,这里不防御
        —— 由 client 契约保证)。
        """
        for msg in req.messages:
            if msg.role != "user":
                continue
            content = msg.content if isinstance(msg.content, list) else [{"type": "text", "text": msg.content}]
            await repo.append_message(conversation_id, role="user", content=content)

    @staticmethod
    async def _load_history_as_messages(repo: ConversationRepo, conversation_id: str) -> list[Message]:
        """SELECT messages → list[Message](Claude 形态,直接喂 Provider)。"""
        rows = await repo.load_messages_as_anthropic(conversation_id)
        return [Message(role=row["role"], content=row["content"]) for row in rows]

    # ---- default tools 注入 ----

    def _normalize_request(self, req: ChatRequest, provider: BaseProvider) -> ChatRequest:
        """Apply agent-side defaults, then trim fields unsupported by the provider."""

        req_with_tools = self._inject_default_tools(req)
        return normalize_request(req_with_tools, provider.capabilities)

    def _inject_default_tools(self, req: ChatRequest) -> ChatRequest:
        """req.tools 是 None → 挂当前装载的所有 tool schema;
        req.tools 是 [] → 关闭工具调用(透传);
        req.tools 是 list → 用调用方指定的(透传)。
        """
        if req.tools is not None:
            return req
        if not self._tools:
            return req  # 没装载 tool,保持 None
        schemas: list[ToolSchema] = [self._tool_schema(tool) for tool in self._tools.values()]
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
    ) -> ChatRequest:
        """Compose the active prompt bundle into `system`, then normalize request fields."""
        composed = req
        if self._sessionmaker is not None:
            from chariot.prompt.composer import PromptComposer
            from chariot.repos.prompt_repo import PromptRepo

            async with self._sessionmaker() as session:
                active_bundle = await PromptRepo(session).get_active_bundle()
                if active_bundle is not None:
                    existing_system = req.system if isinstance(req.system, str) else None
                    system = PromptComposer.render_layers_text(
                        active_bundle.layers,
                        existing_system=existing_system,
                        memory_entries=memory_entries,
                        memory_policy=memory_policy.describe() if memory_policy is not None else None,
                    )
                    composed = dataclasses.replace(req, system=system)
        return self._normalize_request(composed, provider)

    async def _load_memory_entries(
        self,
        session: AsyncSession,
        req: ChatRequest,
        provider: BaseProvider,
        policy: MemoryPolicy,
    ) -> list[dict[str, Any]] | None:
        from chariot.repos.memory_repo import MemoryRepo

        entries = await MemoryRepo(session).list_relevant_entries(
            conversation_id=req.conversation_id,
            provider_name=provider.config.name,
            limit=policy.max_items,
            policy=policy,
        )
        entries = [
            {
                "id": entry.id,
                "kind": entry.kind,
                "text": entry.text,
                "meta": entry.meta,
                "pinned": entry.pinned,
                "archived": entry.archived,
            }
            for entry in entries
        ]
        return entries or None

    @staticmethod
    async def _record_prompt_trace(
        session: AsyncSession,
        req: ChatRequest,
        provider: BaseProvider,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: MemoryPolicy | None = None,
    ) -> Any:
        from chariot.repos.prompt_repo import PromptRepo

        return await PromptRepo(session).record_trace(
            req,
            provider_name=provider.config.name,
            model=provider.config.model,
            memory_entries=memory_entries,
            memory_policy=memory_policy.describe() if memory_policy is not None else None,
        )

    async def _capture_memory(
        self,
        session: AsyncSession,
        *,
        req: ChatRequest,
        provider: BaseProvider,
        memory_policy: MemoryPolicy,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> None:
        from chariot.repos.memory_repo import MemoryRepo

        capture = MemoryCaptureService(MemoryRepo(session))
        await capture.capture_turn(
            req=req,
            provider_name=provider.config.name,
            policy=memory_policy,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )

    async def _capture_error_memory(
        self,
        session: AsyncSession,
        *,
        req: ChatRequest,
        provider: BaseProvider,
        memory_policy: MemoryPolicy,
        error_event: ChatEvent,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> None:
        from chariot.repos.memory_repo import MemoryRepo

        capture = MemoryCaptureService(MemoryRepo(session))
        if error_event.error_type is None or error_event.error_message is None:
            return
        await capture.capture_error(
            conversation_id=req.conversation_id,
            provider_name=provider.config.name,
            error_type=error_event.error_type,
            error_message=error_event.error_message,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )
