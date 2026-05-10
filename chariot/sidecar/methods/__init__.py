"""sidecar business method handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import (
    ConfigError,
    ConversationNotFound,
    DuplicateConversationId,
    DuplicateProviderName,
    ProviderNotFound,
    ToolNotFound,
)
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = ["register_methods"]


class SidecarAgent(Protocol):
    def run_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]: ...

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]: ...


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
            except (ConversationNotFound, ProviderNotFound, ToolNotFound) as e:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, str(e)) from e
            except (DuplicateConversationId, DuplicateProviderName) as e:
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


def register_methods(
    server: JsonRpcServer,
    agent: SidecarAgent,
    *,
    db_path: Path,
    session_key: str | None = None,
) -> None:
    runtime = SidecarRuntime(agent, db_path=db_path, session_key=session_key)

    from chariot.sidecar.methods.chat import ChatMethod
    from chariot.sidecar.methods.conversation import ConversationMethods
    from chariot.sidecar.methods.log import LogMethods
    from chariot.sidecar.methods.prompt import PromptMethods
    from chariot.sidecar.methods.provider import ProviderMethods
    from chariot.sidecar.methods.tool import ToolMethods

    server.method("chat")(ChatMethod(runtime))

    conversations = ConversationMethods(runtime)
    server.method("list_conversations")(conversations.list_)
    server.method("get_conversation")(conversations.get)
    server.method("rename_conversation")(conversations.rename)
    server.method("delete_conversation")(conversations.delete)
    server.method("list_convos")(conversations.list_)
    server.method("get_convo")(conversations.get)
    server.method("rename_convo")(conversations.rename)
    server.method("delete_convo")(conversations.delete)

    tools = ToolMethods(runtime)
    server.method("list_tools")(tools.list_)
    server.method("enable_tool")(tools.enable)
    server.method("disable_tool")(tools.disable)
    server.method("config_tool")(tools.config)

    providers = ProviderMethods(runtime)
    server.method("list_providers")(providers.list_)
    server.method("add_provider")(providers.add)
    server.method("update_provider")(providers.update)
    server.method("delete_provider")(providers.delete)
    server.method("probe_provider")(providers.probe)

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
