"""AIAgent — 0.6.0 内核主入口。

替代 0.5.0 `chariot/server/agent.py` 的 `Agent` 类(`Agent` → `AIAgent`,
明确"AI 主体" vs 一般意义"代理 / 客户端")。

职责:
1. 持有 `name → BaseProvider` 实例字典(从 DB models 表装载)
2. 持有 `name → BaseTool` 实例字典(从 DB tools 表装载)
3. `run(req)` 主入口:路由 + default tools 注入 + 锁包装 + 委托 AgentLoop

差异(vs 0.5.0 Agent):
- 输入:`ChatRequest`(typed,跟 Claude API 1:1)而非 raw bytes
- 输出:`AsyncIterator[ChatEvent]`(协议无关)而非 fastapi `Response`
- **撤 fast / slow path 二分**:0.6.0 全部走 streaming AgentLoop
- 错误传播:全部转 `ChatEvent(kind="error", error_type=...)` yield 给 surface
  (上层 server / sidecar / CLI 各自映射成自己的错误形态)

详见 `docs/DESIGN.md` §6.1。

模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message, ToolSchema
from chariot.agent.convo_lock import ConvoLockManager
from chariot.agent.exceptions import ConvoLockTimeout
from chariot.agent.loop import AgentLoop

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.providers.base import BaseProvider
    from chariot.repos.convo_repo import ConvoRepo
    from chariot.tools.base import BaseTool


class AIAgent:
    """0.6.0 内核入口。每个 surface 进程构造一个实例。

    使用方式::

        # 生产路径(CLI / sidecar / Gateway 进程启动时)
        agent = await AIAgent.from_db(db_path)
        async for event in agent.run(req):
            # 消费 ChatEvent

        # 测试路径(直接注入 mock providers / tools)
        agent = AIAgent(
            providers={"mock": MockProvider.from_options({})},
            tools={},
            sessionmaker=test_sm,
        )
    """

    # 单例(每进程一个);`from_db` 装载完写入,`uninstall` 清空
    _current: ClassVar[AIAgent | None] = None

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

    # ---- 装载 / 单例 ----

    @classmethod
    async def from_db(cls, db_path: Path) -> Self:
        """从 ~/.chariot/chariot.db 装载 ProviderEntry / ToolEntry,构造所有
        Provider / Tool 实例,返就绪 AIAgent 并设为 `_current` 单例。

        首次启动表为空 → 自动 seed:`providers` 插一条 mock entry,`tools` 插
        4 条 disabled fixture(read_file / list_dir / shell_exec / http_get)。
        seeded 工具默认 disabled,所以新装的 AIAgent 无 tool;mock provider
        已可用。

        每个 surface 进程启动时调一次。
        """
        from chariot.agent.config import ChariotConfig, ToolConfig
        from chariot.database.session import init_db
        from chariot.providers.registry import ProviderRegistry
        from chariot.repos.provider_repo import ProviderRepo
        from chariot.repos.tool_repo import ToolRepo
        from chariot.tools.registry import ToolRegistry

        sm = await init_db(db_path)
        async with sm() as session:
            await ProviderRepo(session).seed_if_empty()
            await ToolRepo(session).seed_if_empty()
            cfg = await ChariotConfig.from_db(session)
            tool_cfg = await ToolConfig.from_db(session)

        providers: dict[str, BaseProvider] = {
            entry.name: ProviderRegistry.build(entry.type, entry.options) for entry in cfg.providers
        }
        tools: dict[str, BaseTool] = {
            entry.name: ToolRegistry.build(entry) for entry in tool_cfg.tools
        }

        instance = cls(providers=providers, tools=tools, sessionmaker=sm)
        cls._current = instance
        return instance

    @classmethod
    def current(cls) -> AIAgent:
        """获取当前进程单例;未装载抛 RuntimeError。"""
        if cls._current is None:
            raise RuntimeError("AIAgent 未装载;先调 AIAgent.from_db(db_path)")
        return cls._current

    @classmethod
    def uninstall(cls) -> None:
        """清空单例(测试 / shutdown 用)。"""
        cls._current = None

    # ---- 资源访问(供 surface 直调 repo) ----

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]:
        """暴露 sessionmaker 给 surface(CLI / sidecar)直调 repo;未装载抛 RuntimeError。

        典型用法::

            async with agent.session_maker() as session:
                repo = ConvoRepo(session)
                entries = await repo.list_entries()
        """
        if self._sessionmaker is None:
            raise RuntimeError("AIAgent 未装载 sessionmaker;先调 AIAgent.from_db(db_path)")
        return self._sessionmaker

    @property
    def providers(self) -> dict[str, BaseProvider]:
        """已装载的 provider 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._providers)

    @property
    def tools(self) -> dict[str, BaseTool]:
        """已装载的 tool 字典(只读视图;surface 仅用于 status / 列表展示)。"""
        return dict(self._tools)

    # ---- per-call provider 覆盖(CLI flag 用) ----

    async def patch_provider_options(
        self,
        provider_name: str,
        *,
        options_overrides: dict[str, str],
    ) -> None:
        """对指定 provider entry 临时合并 options 重建实例(本进程生效)。

        典型场景:CLI `chariot chat --model X --api-key Y` 把 X / Y 注入到
        DB entry 的 options 后重建 Provider。`options_overrides` 浅 merge 到
        `entry.options`,覆盖同名字段;空 dict 直接 no-op。

        失败处理:
        - `provider_name` 不在已装载 providers → 静默 no-op(让后续 AIAgent.run
          路由阶段统一发 unknown_provider error,文案一致)
        - DB 里 entry 不存在(理论上不应发生,因为它能装载就说明在过)→ no-op
        - `Provider.from_options(merged)` 抛 ConfigError(覆盖值非法)→ 透传
          给 caller,CLI 侧 die 提示
        """
        if not options_overrides:
            return
        if provider_name not in self._providers:
            return

        # 惰性 import 避循环依赖;CLI 侧调用频率低,单次 import 开销可忽略
        from chariot.providers.registry import ProviderRegistry
        from chariot.repos.provider_repo import ProviderRepo

        async with self.session_maker() as session:
            entry = await ProviderRepo(session).get_entry(provider_name)
        if entry is None:
            return

        merged_options = {**entry.options, **options_overrides}
        # ConfigError 由 caller 处理(CLI 应翻译成 die 提示)
        new_provider = ProviderRegistry.build(entry.type, merged_options)
        self._providers[provider_name] = new_provider

    # ---- 主入口 ----

    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 chat,yield ChatEvent 流(详见 DESIGN §6.1 / §6.4)。

        路由:按 `req.provider_name`(entry name)找 Provider 实例;缺失 →
        yield error event 退出。
        default tools 注入:`req.tools is None` → 挂所有装载 tool 的 schema。
        stateful(`req.convo_id` 非空)→ 在 convo lock 内开 session 跑;
        stateless → 直接跑 AgentLoop 不开 session。
        """
        provider = self._providers.get(req.provider_name)
        if provider is None:
            yield ChatEvent.error_event(
                error_type="unknown_provider",
                error_message=(
                    f"unknown provider entry {req.provider_name!r}; "
                    f"known: {sorted(self._providers.keys())}"
                ),
            )
            return

        effective_req = self._inject_default_tools(req)

        if req.is_stateful():
            async for event in self._run_stateful(effective_req, provider):
                yield event
        else:
            async for event in self._run_stateless(effective_req, provider):
                yield event

    async def _run_stateless(
        self, req: ChatRequest, provider: BaseProvider
    ) -> AsyncIterator[ChatEvent]:
        """无 convo_id:直接跑 AgentLoop,不锁不持久化。"""
        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            repo=None,
            convo_id=None,
        )
        async for event in loop.run(req):
            yield event

    async def _run_stateful(
        self, req: ChatRequest, provider: BaseProvider
    ) -> AsyncIterator[ChatEvent]:
        """有 convo_id:开 session + 进 convo lock + load history + AgentLoop。

        约定:`req.messages` 是**新增的消息**(通常 1 条 user message);
        server 端 load 历史 prepend。stateful 调用方不发完整对话历史。
        """
        if self._sessionmaker is None:
            yield ChatEvent.error_event(
                error_type="no_sessionmaker",
                error_message="AIAgent 未装载 sessionmaker;stateful 模式需 from_db 装载",
            )
            return

        convo_id = req.convo_id
        assert convo_id is not None  # is_stateful() 保证

        async with self._sessionmaker() as session:
            try:
                async with ConvoLockManager.acquire(convo_id, db_session=session):
                    async for event in self._stateful_critical_section(
                        req, convo_id, provider, session
                    ):
                        yield event
            except ConvoLockTimeout as e:
                yield ChatEvent.error_event(
                    error_type=f"convo_busy_{e.layer}",
                    error_message=str(e),
                )

    async def _stateful_critical_section(
        self,
        req: ChatRequest,
        convo_id: str,
        provider: BaseProvider,
        session: AsyncSession,
    ) -> AsyncIterator[ChatEvent]:
        """convo lock 内的实际工作:ensure convo / persist new user / load history /
        跑 AgentLoop。"""
        from chariot.repos.convo_repo import ConvoRepo

        repo = ConvoRepo(session)
        await repo.ensure_exists(convo_id)
        await self._persist_new_user_messages(repo, convo_id, req)
        history = await self._load_history_as_messages(repo, convo_id)
        full_req = dataclasses.replace(req, messages=history)

        loop = AgentLoop(
            provider=provider,
            tools=self._tools,
            repo=repo,
            convo_id=convo_id,
        )
        async for event in loop.run(full_req):
            yield event

    @staticmethod
    async def _persist_new_user_messages(repo: ConvoRepo, convo_id: str, req: ChatRequest) -> None:
        """req.messages 里新增的 user 消息(末尾若干条 role='user')落库。

        简化策略:把 req.messages 整体当"新增"持久化(假设 client 在 stateful
        模式下只发新增消息;若发了完整 history,下一轮 load 会重复,这里不防御
        —— 由 client 契约保证)。
        """
        for msg in req.messages:
            if msg.role != "user":
                continue
            content = (
                msg.content
                if isinstance(msg.content, list)
                else [{"type": "text", "text": msg.content}]
            )
            await repo.append_message(convo_id, role="user", content=content)

    @staticmethod
    async def _load_history_as_messages(repo: ConvoRepo, convo_id: str) -> list[Message]:
        """SELECT messages → list[Message](Claude 形态,直接喂 Provider)。"""
        rows = await repo.load_messages_as_anthropic(convo_id)
        return [Message(role=row["role"], content=row["content"]) for row in rows]

    # ---- default tools 注入 ----

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
