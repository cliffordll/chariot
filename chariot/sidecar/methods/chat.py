"""sidecar `chat` method handler(0.6.5 S.8b · 0.6.6 加 per-call override)。

`ChatMethod`:实例承载 `agent` 引用 + `db_path`(给 per-call override 路径走
`AgentRegistry.reserve` 用);`__call__` 暴露 RPC handler 签名。
跑一次 chat → 流式 notify ChatEvent + 终止 response。

`_RequestDecoder`:RPC params dict → typed `ChatRequest`,字段错抛 `RpcError`。
RPC 反序列化是 sidecar 特有的事(ChatRequest 自身不该耦合 RPC 错误码),
所以解码逻辑住这。文件内私有(下划线前缀),只有 `ChatMethod` 调用。

per-call override(0.6.6+):params 接收可选 `base_url` / `api_key`,任一非空
→ 算 `session_key = "sidecar:override:<provider>:<sha16(options)>"`,走
`AgentRegistry.reserve` 拿 per-call AIAgent(LRU 32 自动管理,httpx client
经 ClientCache 命中复用)。**model** 走 `ChatRequest.model` 走原路径(不重建
Provider),跟 CLI 的拆分一致(详 `chariot/cli/commands/chat.py` 顶部 docstring)。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, cast

from chariot.agent.chat_request import ChatRequest, Message
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase, SidecarAgent


class ChatMethod(MethodBase):
    """sidecar `chat` method handler。`__call__` 暴露 handler 签名。

    持 `agent`(默认 sidecar agent)+ `db_path`(给 per-call override 路径用)。
    无 base_url / api_key 时走默认 agent;有则走 `AgentRegistry.reserve`。
    """

    def __init__(self, agent: SidecarAgent, *, db_path: Path) -> None:
        super().__init__(agent)
        self._db_path = db_path

    async def __call__(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """跑一次 chat,流式 notify ChatEvent + 终止 response。

        流程:
        1. params dict → ChatRequest(_RequestDecoder.parse)
        2. resolve agent — 默认走 self.agent;params 含 base_url / api_key
           → AgentRegistry.reserve 拿 per-call agent
        3. agent.run(req) 流式产 ChatEvent
        4. 每个 event 走 ctx.notify("chat_event", asdict(event))
        5. 流结束返 {stream_id, ended_at}

        params 缺关键字段 / 类型错 → 抛 RpcError(ERR_INVALID_PARAMS)。
        """
        req = _RequestDecoder.parse(params)
        base_url = self._optional_str(params, "base_url")
        api_key = self._optional_str(params, "api_key")
        agent = await self._resolve_agent(req.provider_name, base_url, api_key)

        stream_id = uuid.uuid4().hex
        async for event in agent.run(req):
            await ctx.notify("chat_event", dataclasses.asdict(event))
        return {
            "stream_id": stream_id,
            "ended_at": time.time(),  # epoch 浮点秒;前端按需转 ISO
        }

    async def _resolve_agent(
        self,
        provider_name: str,
        base_url: str | None,
        api_key: str | None,
    ) -> SidecarAgent:
        """无 override → 返默认 agent;有 → 走 AgentRegistry.reserve 拿 per-call。

        session_key 由 (provider_name, sorted options) hash 决定:同 override
        命中已缓存的 agent(LRU 32),不同 override 各自独立缓存。Provider 实例
        重建零成本(只持 _options + _spec),httpx client 经 ClientCache 按
        ClientSpec 命中复用。
        """
        if base_url is None and api_key is None:
            return self.agent

        # 惰性 import 避循环(registry → AIAgent → tools/providers)
        from chariot.agent.registry import AgentRegistry

        options: dict[str, str] = {}
        if base_url is not None:
            options["base_url"] = base_url
        if api_key is not None:
            options["api_key"] = api_key

        # 16 字符 sha256 prefix 足够区分 — 撞库不是威胁,人为指定的 (base_url,
        # api_key) 组合是有限的;LRU 32 满了自然淘汰
        digest = hashlib.sha256(
            json.dumps(options, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        session_key = f"sidecar:override:{provider_name}:{digest}"

        return await AgentRegistry.reserve(
            session_key,
            db_path=self._db_path,
            provider_overrides={provider_name: options},
        )


class _RequestDecoder:
    """RPC params dict → typed `ChatRequest`;字段错抛 `RpcError`。

    全 classmethod / staticmethod 形态(无实例状态),纯 namespace 类。
    只校验"必填字段缺 / 顶层类型错"这种粗粒度;细粒度字段语义错由
    ChatRequest 构造或下游 Provider 报。
    """

    # ChatRequest 里的可选字段(顺序跟 chat_request.py 字段顺序一致)
    _OPTIONAL_FIELDS = (
        "model",
        "max_tokens",
        "system",
        "tools",
        "tool_choice",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "metadata",
        "thinking",
        "convo_id",
        "agent_id",
    )

    @classmethod
    def parse(cls, params: dict[str, Any]) -> ChatRequest:
        """params dict → ChatRequest;字段错抛 RpcError(ERR_INVALID_PARAMS)。"""
        pn = params.get("provider_name")
        if not isinstance(pn, str) or not pn:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'provider_name' is required and must be a non-empty string",
            )

        msgs_raw = params.get("messages")
        if not isinstance(msgs_raw, list) or not msgs_raw:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'messages' is required and must be a non-empty list",
            )
        msgs_list = cast(list[Any], msgs_raw)

        messages = [cls._message(m, idx=i) for i, m in enumerate(msgs_list)]

        kwargs: dict[str, Any] = {"provider_name": pn, "messages": messages}
        for key in cls._OPTIONAL_FIELDS:
            if key in params:
                kwargs[key] = params[key]

        try:
            return ChatRequest(**kwargs)
        except (TypeError, ValueError) as e:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"invalid chat request: {e}",
            ) from e

    @staticmethod
    def _message(raw: object, *, idx: int) -> Message:
        """单条 message dict → Message;格式错抛 RpcError。"""
        if not isinstance(raw, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}] must be an object",
            )
        m = cast(dict[str, Any], raw)
        role_raw = m.get("role")
        content_raw = m.get("content")
        if role_raw not in ("user", "assistant"):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}].role must be 'user' or 'assistant', got {role_raw!r}",
            )
        if not isinstance(content_raw, str | list):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}].content must be a string or list of blocks",
            )
        return Message(
            role=role_raw,
            content=content_raw
            if isinstance(content_raw, str)
            else cast("list[dict[str, Any]]", content_raw),
        )
