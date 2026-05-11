"""sidecar business method handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import (
    AuxiliaryClientNotFound,
    ConfigError,
    ConversationNotFound,
    DuplicateAuxiliaryClientName,
    DuplicateConversationId,
    DuplicateProviderName,
    ProviderNotFound,
    ToolNotFound,
)
from chariot.models.agent import UNSET, ClearableStr
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.agent.reflection import CriticAgent
    from chariot.audit import AuditHookManager
    from chariot.guardrails import GuardrailEngine

__all__ = ["register_methods"]


class SidecarAgent(Protocol):
    def run_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]: ...

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]: ...

    @property
    def critic_agent(self) -> CriticAgent | None: ...

    @property
    def guardrail_engine(self) -> GuardrailEngine | None: ...

    @property
    def audit_hooks(self) -> AuditHookManager: ...


class MethodBase:
    """Shared helper for sidecar method handlers."""

    def __init__(self, runtime: SidecarRuntime) -> None:
        self.runtime = runtime
        self.agent = runtime.agent

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.runtime.session_maker() as session:
            try:
                yield session
            except (
                ConversationNotFound,
                ProviderNotFound,
                ToolNotFound,
                AuxiliaryClientNotFound,
            ) as e:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, str(e)) from e
            except (
                DuplicateConversationId,
                DuplicateProviderName,
                DuplicateAuxiliaryClientName,
            ) as e:
                raise RpcError(JsonRpcServer.ERR_DUPLICATE, str(e)) from e
            except ConfigError as e:
                raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(e)) from e

    @staticmethod
    def _require_str(params: dict[str, Any], key: str) -> str:
        val = params.get(key)
        if not isinstance(val, str) or not val:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} is required and must be a non-empty string",
            )
        return val

    @staticmethod
    def _require_dict(params: dict[str, Any], key: str) -> dict[str, Any]:
        val = params.get(key)
        if not isinstance(val, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} is required and must be an object",
            )
        return cast(dict[str, Any], val)

    @staticmethod
    def _optional_dict(params: dict[str, Any], key: str) -> dict[str, Any] | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be an object or null",
            )
        return cast(dict[str, Any], val)

    @staticmethod
    def _optional_str(params: dict[str, Any], key: str) -> str | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, str):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be a string or null",
            )
        return val

    @staticmethod
    def _clearable_str(params: dict[str, Any], key: str) -> ClearableStr:
        """三态读取:key 缺席 → UNSET(skip);key=null → None(清空);key=str → set。

        给 update 路径用,区分"字段没传"和"显式清空"。普通 `_optional_str` 把
        二者都归到 None,导致前端无法清空字段。
        """
        if key not in params:
            return UNSET
        val = params[key]
        if val is None:
            return None
        if not isinstance(val, str):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be a string or null",
            )
        return val


def register_methods(
    server: JsonRpcServer,
    agent: SidecarAgent,
    *,
    db_path: Path,
    session_key: str | None = None,
) -> None:
    runtime = SidecarRuntime(agent, db_path=db_path, session_key=session_key)

    from chariot.sidecar.methods.agent import AgentMethods
    from chariot.sidecar.methods.audit import AuditMethods
    from chariot.sidecar.methods.auxiliary import AuxiliaryMethods
    from chariot.sidecar.methods.chat import ChatMethod
    from chariot.sidecar.methods.context import ContextMethods
    from chariot.sidecar.methods.conversation import ConversationMethods
    from chariot.sidecar.methods.critic import CriticMethods
    from chariot.sidecar.methods.eval import EvalMethods
    from chariot.sidecar.methods.guardrail import GuardrailMethods
    from chariot.sidecar.methods.job import JobMethods
    from chariot.sidecar.methods.log import LogMethods
    from chariot.sidecar.methods.memory import MemoryMethods
    from chariot.sidecar.methods.prompt import PromptMethods
    from chariot.sidecar.methods.provider import ProviderMethods
    from chariot.sidecar.methods.task import TaskMethods
    from chariot.sidecar.methods.tool import ToolMethods
    from chariot.sidecar.methods.toolset import ToolsetMethods
    from chariot.sidecar.methods.trace import TraceMethods

    server.method("chat")(ChatMethod(runtime))

    contexts = ContextMethods(runtime)
    server.method("list_context_snapshots")(contexts.list_)
    server.method("get_context_snapshot")(contexts.get)
    server.method("list_context_traces")(contexts.traces)
    server.method("inspect_context")(contexts.inspect)

    memories = MemoryMethods(runtime)
    server.method("list_memories")(memories.list_)
    server.method("get_memory")(memories.show)
    server.method("create_memory")(memories.add)
    server.method("update_memory")(memories.update)
    server.method("delete_memory")(memories.delete)
    server.method("pin_memory")(memories.pin)
    server.method("archive_memory")(memories.archive)
    server.method("list_memory_events")(memories.events)
    server.method("list_memory_links")(memories.links)
    server.method("search_memory")(memories.search)

    conversations = ConversationMethods(runtime)
    server.method("list_conversations")(conversations.list_)
    server.method("get_conversation")(conversations.get)
    server.method("rename_conversation")(conversations.rename)
    server.method("delete_conversation")(conversations.delete)
    server.method("list_convos")(conversations.list_)
    server.method("get_convo")(conversations.get)
    server.method("rename_convo")(conversations.rename)
    server.method("delete_convo")(conversations.delete)
    server.method("search_conversation")(conversations.search)
    server.method("rebuild_conversation_fts")(conversations.rebuild_fts)

    auxiliaries = AuxiliaryMethods(runtime)
    server.method("list_auxiliary_clients")(auxiliaries.list_)
    server.method("show_auxiliary_client")(auxiliaries.show)
    server.method("add_auxiliary_client")(auxiliaries.add)
    server.method("update_auxiliary_client")(auxiliaries.update)
    server.method("delete_auxiliary_client")(auxiliaries.delete)

    critics = CriticMethods(runtime)
    server.method("critique_text")(critics.critique)
    server.method("get_critic_config")(critics.config)

    guardrails = GuardrailMethods(runtime)
    server.method("list_guardrails")(guardrails.list_)
    server.method("try_guardrail")(guardrails.try_)

    audits = AuditMethods(runtime)
    server.method("list_audit_events")(audits.list_)
    server.method("get_audit_event")(audits.show)

    tools = ToolMethods(runtime)
    server.method("list_tools")(tools.list_)
    server.method("show_tool")(tools.show)
    server.method("enable_tool")(tools.enable)
    server.method("disable_tool")(tools.disable)
    server.method("config_tool")(tools.config)
    server.method("probe_tool")(tools.probe)

    toolsets = ToolsetMethods(runtime)
    server.method("list_toolsets")(toolsets.list_)
    server.method("get_toolset")(toolsets.show)
    server.method("create_toolset")(toolsets.add)
    server.method("update_toolset")(toolsets.update)
    server.method("delete_toolset")(toolsets.delete)
    server.method("add_toolset_member")(toolsets.add_member)
    server.method("remove_toolset_member")(toolsets.remove_member)

    providers = ProviderMethods(runtime)
    server.method("list_providers")(providers.list_)
    server.method("show_provider")(providers.show)
    server.method("add_provider")(providers.add)
    server.method("update_provider")(providers.update)
    server.method("delete_provider")(providers.delete)
    server.method("use_provider")(providers.use)
    server.method("probe_provider")(providers.probe)
    server.method("get_provider_status")(providers.status)

    agents = AgentMethods(runtime)
    server.method("list_agents")(agents.list_agents)
    server.method("get_agent")(agents.get_agent)
    server.method("create_agent")(agents.create_agent)
    server.method("update_agent")(agents.update_agent)
    server.method("delete_agent")(agents.delete_agent)

    tasks = TaskMethods(runtime)
    server.method("list_tasks")(tasks.list_tasks)
    server.method("get_task")(tasks.get_task)
    server.method("get_task_run")(tasks.get_task_run)
    server.method("list_task_runs")(tasks.list_task_runs)
    server.method("create_task")(tasks.create)
    server.method("pause_task")(tasks.pause)
    server.method("resume_task")(tasks.resume)
    server.method("cancel_task")(tasks.cancel)
    server.method("start_task_run")(tasks.start_run)
    server.method("complete_task_run")(tasks.complete_run)
    server.method("fail_task_run")(tasks.fail_run)
    server.method("cancel_task_run")(tasks.cancel_run)
    server.method("delegate_task")(tasks.delegate)

    jobs = JobMethods(runtime)
    server.method("list_jobs")(jobs.list_jobs)
    server.method("show_job")(jobs.show_job)
    server.method("create_job")(jobs.create_job)
    server.method("update_job")(jobs.update_job)
    server.method("enable_job")(jobs.enable_job)
    server.method("disable_job")(jobs.disable_job)
    server.method("delete_job")(jobs.delete_job)
    server.method("run_job_now")(jobs.run_job_now)

    prompts = PromptMethods(runtime)
    server.method("list_prompt_bundles")(prompts.list_)
    server.method("get_prompt_bundle")(prompts.show)
    server.method("list_prompt_versions")(prompts.versions)
    server.method("get_prompt_version")(prompts.version)
    server.method("list_prompt_traces")(prompts.traces)
    server.method("inspect_prompt")(prompts.inspect)
    server.method("add_prompt_bundle")(prompts.add)
    server.method("update_prompt_bundle")(prompts.update)
    server.method("activate_prompt_bundle")(prompts.activate)

    logs = LogMethods(runtime)
    server.method("list_logs")(logs.list_)

    traces = TraceMethods(runtime)
    server.method("list_traces")(traces.list_)
    server.method("get_trace_turn")(traces.show)
    server.method("view_trace_tree")(traces.view)
    server.method("reconcile_traces")(traces.reconcile)

    evals = EvalMethods(runtime)
    server.method("list_golden_tasks")(evals.list_golden_tasks)
    server.method("list_eval_runs")(evals.list_eval_runs)
    server.method("get_eval_run")(evals.get_eval_run)
    server.method("diff_eval_runs")(evals.diff_eval_runs)
