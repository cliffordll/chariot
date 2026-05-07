"""Agent — chariot 智能体主类。

职责(0.5.0 协议级流式工具循环)
--------------------------------
1. 持有 `name → Model` 实例字典(从 DB entries 构造)+ `name → Tool` 实例字典
2. **fast path**(无 tools + 无 conversation):0.3.1 行为,按 body.model 路由,
   单次 `model.respond(body)` 透传
3. **slow path**(有 tools 或 有 conversation_id):**全程 streaming**
   - 可选 load 历史 messages,prepend 到 body.messages
   - 每轮 `model.respond(body, stream=True)` → 边转发上游 SSE 边 buffer assistant
     content blocks(文本 + tool_use)
   - `message_stop` 后判定:有 tool_use → 执行 → server 合成 `tool_result` message
     的 SSE 帧推给 client → 拼下轮 body 续循环;无 tool_use → 关流
   - 一条 HTTP 响应里串多个 `message_start ... message_stop` 块,client(SDK /
     UI / 透传 Anthropic SDK)实时看到中间 tool_use / tool_result(详见 DESIGN §4)
4. 每次 handle 落一条 `logs` 记录(latency 是整个 handle 的 total time)

`install_from_config` 是幂等的全量重置 —— 既是首次安装,也是配置变更后的重建。

工具循环 max_iter 通过 env `CHARIOT_MAX_TOOL_ITER` 配置,默认 10;超限发
`event: error` SSE + 关流(stream client),或 raise `ServiceError(400)`(非
stream client)。

非 stream client 进 slow path:server 内部仍 streaming,把生成器 drain 后从
`_TurnState.final` 重建 Anthropic 非流响应 JSON 返。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fastapi.responses import Response, StreamingResponse
from ulid import ULID

from chariot.agent.config import ChariotConfig, ToolConfig
from chariot.agent.conversation_lock import ConversationLockManager
from chariot.repos.conversation_repo import ConversationRepo
from chariot.repos.log_writer import log_writer
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.shared.sse import SseParser
from chariot.tools.base import BaseTool
from chariot.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("chariot.server.agent")

_DEFAULT_MAX_TOOL_ITER = 10


@dataclass
class _TurnState:
    """一次 LLM streaming turn 的解析状态:为 detect tool_use / 持久化 / 非 stream
    client 的最终 JSON 重建,需要把上游 SSE 帧累积成 assistant content blocks。

    每个 turn 起步时新建,turn 结束(message_stop)落定。
    """

    msg_id: str = ""
    msg_model: str = ""
    role: str = "assistant"
    content: list[dict[str, Any]] = field(default_factory=list)  # type: ignore[assignment]
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # 流式累积:idx → text 增量列表 / tool_use input partial JSON 字符串
    text_buf: dict[int, list[str]] = field(default_factory=dict)  # type: ignore[assignment]
    tool_input_buf: dict[int, str] = field(default_factory=dict)  # type: ignore[assignment]
    # 当前正在累积的 block(content_block_start 后到 content_block_stop 前)
    current_block: dict[str, Any] | None = None
    current_index: int = -1


class Agent:
    """chariot 智能体。0.5.0 起 slow path 全程 streaming + 多轮工具循环串在
    一条响应里。"""

    # ---- 类级单例 ----

    _current: ClassVar[Agent | None] = None

    # ---- 实例构造 ----

    def __init__(self) -> None:
        self.models: dict[str, Model] = {}
        self.tools: dict[str, BaseTool] = {}

    # ---- 单例管理 ----

    @classmethod
    def install_from_config(
        cls,
        model_config: ChariotConfig,
        tool_config: ToolConfig | None = None,
    ) -> Agent:
        """全量重置:rebuild Model + Tool 字典,设为 current。

        - `tool_config=None` 等价 ToolConfig.empty()(0.3.x 调用方不传第二参时兼容)
        - 每条 entry eager build,起步成本与 entry 数线性
        - lifespan startup / admin CRUD 后调即可,幂等
        """
        agent = cls()
        for entry in model_config.models:
            agent.models[entry.name] = ModelRegistry.build(entry)
        if tool_config is not None:
            for tool_entry in tool_config.tools:
                agent.tools[tool_entry.name] = ToolRegistry.build(tool_entry)
        cls._current = agent
        _log.info(
            "agent installed: models=%s tools=%s",
            list(agent.models),
            list(agent.tools),
        )
        return agent

    @classmethod
    def current(cls) -> Agent:
        if cls._current is None:
            raise RuntimeError(
                "Agent 未安装;在 app lifespan startup 里调 Agent.install_from_config()"
            )
        return cls._current

    @classmethod
    def uninstall(cls) -> None:
        cls._current = None

    # ---- 请求处理 ----

    async def handle(
        self,
        body: bytes,
        *,
        session: AsyncSession | None = None,
        conversation_id: str | None = None,
    ) -> Response:
        """处理一次聊天请求。

        - `session` / `conversation_id` 仅 slow path 用;fast path 完全不碰
        - body.model 缺失 / 未知 → 400 unknown_model_name(沿用 0.3.1)
        - 工具循环超限 → stream client 发 SSE error 帧 + 关流;非 stream client
          400 tool_iter_exceeded
        """
        t0 = time.monotonic()
        body_dict = self._parse_body_dict(body)
        model_name = self._extract_model_name(body_dict)
        is_stream = self._detect_stream(body_dict)

        if not model_name:
            err = ServiceError(
                status=400,
                code="unknown_model_name",
                message="body.model 字段必填:写 chariot 的 entry name(`chariot model list` 看可用)",
            )
            await self._record_log(None, "error", t0, error=f"{err.code}: {err.message}")
            raise err

        model = self.models.get(model_name)
        if model is None:
            known = ", ".join(self.models) or "(空)"
            err = ServiceError(
                status=400,
                code="unknown_model_name",
                message=f"未知 model name: {model_name!r};可选:{known}",
            )
            await self._record_log(model_name, "error", t0, error=f"{err.code}: {err.message}")
            raise err

        # Server-side default tools:client 没传 body.tools 时注入所有 enabled tool 的 schema
        if "tools" not in body_dict and self.tools:
            body_dict["tools"] = [t.schema() for t in self.tools.values()]

        has_tools = bool(body_dict.get("tools"))
        has_conv = conversation_id is not None and session is not None

        # Fast path(沿用 0.3.1):无 tools + 无 conversation,直接透传 body bytes
        if not has_tools and not has_conv:
            try:
                resp = await model.respond(body, stream=is_stream)
                await self._record_log(model_name, "ok", t0)
                return resp
            except ServiceError as e:
                await self._record_log(model_name, "error", t0, error=f"{e.code}: {e.message}")
                raise
            except Exception as e:  # pragma: no cover
                await self._record_log(model_name, "error", t0, error=str(e))
                raise

        # Slow path:全程 streaming
        # final 用作"非 stream client 收尾"的载体 —— 生成器在最后一轮 message_stop
        # 后填好,handle drain 完取出来重建 JSON
        final: dict[str, Any] = {}
        iterator = self._stream_tool_loop(
            body_dict=body_dict,
            model=model,
            model_name=model_name,
            session=session if has_conv else None,
            conversation_id=conversation_id if has_conv else None,
            final=final,
            t0=t0,
        )

        if is_stream:
            return StreamingResponse(
                iterator,
                status_code=200,
                media_type="text/event-stream",
            )

        # 非 stream client:消费整个 SSE 流,从 final 重建 Anthropic 非流响应
        try:
            async for _ in iterator:
                pass
        except ServiceError:
            raise
        except Exception as e:  # pragma: no cover
            await self._record_log(model_name, "error", t0, error=str(e))
            raise

        if final.get("error"):
            err_obj = final["error"]
            assert isinstance(err_obj, ServiceError)
            raise err_obj

        if not final.get("content_set"):
            # 极端情况:流空 / 未完成。理论上不可达
            raise ServiceError(
                status=500,
                code="empty_slow_path_stream",
                message="slow path 流空,无最终 message",
            )

        response_dict: dict[str, Any] = {
            "id": final["msg_id"],
            "type": "message",
            "role": "assistant",
            "model": final["msg_model"] or model_name,
            "content": final["content"],
            "stop_reason": final["stop_reason"] or "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": final["input_tokens"],
                "output_tokens": final["output_tokens"],
            },
        }
        body_bytes = json.dumps(response_dict, ensure_ascii=False).encode("utf-8")
        return Response(content=body_bytes, status_code=200, media_type="application/json")

    # ---- Slow path:流式工具循环主流程 ----

    async def _stream_tool_loop(
        self,
        *,
        body_dict: dict[str, Any],
        model: Model,
        model_name: str,
        session: AsyncSession | None,
        conversation_id: str | None,
        final: dict[str, Any],
        t0: float,
    ) -> AsyncIterator[bytes]:
        """流式工具循环。yield SSE 字节给 client;`final` dict 在最后一轮 message_stop
        后被填充(给非 stream client 用),或 max_iter 超时填 `error` ServiceError。

        stateful(conversation_id != None)模式下整个循环包在
        `ConversationLockManager.acquire(conv_id)` 里 —— 同 conv 的并发请求被串行,
        避免两个客户端各自 load history 看到对方写之前的快照(issue 3)。锁超时
        (default 30s,env `CHARIOT_CONV_LOCK_TIMEOUT_S`)→ 503 conversation_busy。
        """
        conv_repo = (
            ConversationRepo(session)
            if (session is not None and conversation_id is not None)
            else None
        )

        # Advisory lock:仅 stateful 时持锁;stateless 走 nullcontext no-op
        lock_ctx = (
            ConversationLockManager.acquire(conversation_id)
            if conversation_id is not None
            else nullcontext()
        )
        async with lock_ctx:
            async for chunk in self._stream_tool_loop_body(
                body_dict=body_dict,
                model=model,
                model_name=model_name,
                conv_repo=conv_repo,
                conversation_id=conversation_id,
                final=final,
                t0=t0,
            ):
                yield chunk

    async def _stream_tool_loop_body(
        self,
        *,
        body_dict: dict[str, Any],
        model: Model,
        model_name: str,
        conv_repo: ConversationRepo | None,
        conversation_id: str | None,
        final: dict[str, Any],
        t0: float,
    ) -> AsyncIterator[bytes]:
        """`_stream_tool_loop` 的循环体本身,跟 lock 解耦方便测试与读;
        整个流程跟 0.4.x 的 `_run_tool_loop` 工具循环逻辑同构,只是从非流式
        + 收尾重发改成全程 streaming(详见 DESIGN §9.1 流程对比图)。"""
        # Stateful 起步:确保 conversation 存在 + load history + persist client 这次发的新 messages
        if conv_repo is not None and conversation_id is not None:
            await conv_repo.ensure_exists(conversation_id)
            history = await conv_repo.load_messages_as_anthropic(conversation_id)
            new_msgs = self._extract_messages(body_dict)
            for msg in new_msgs:
                role = msg.get("role")
                content = msg.get("content")
                if not isinstance(role, str) or content is None:
                    continue
                await conv_repo.append_message(conversation_id, role, content)
            body_dict["messages"] = list(history) + list(new_msgs)

        # 强制 stream=True 给上游 Model;client 的 stream 偏好已被 handle() 用
        # is_stream 拿走,这里 body_dict 只控制上游
        body_dict["stream"] = True

        max_iter = self._max_tool_iter()
        iter_count = 0

        while iter_count < max_iter:
            iter_count += 1
            body_bytes = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
            try:
                upstream_resp = await model.respond(body_bytes, stream=True)
            except ServiceError as e:
                # 上游错误:emit SSE error + 填 final.error
                yield self._format_sse_event(
                    "error",
                    {"type": "error", "error": {"type": e.code, "message": e.message}},
                )
                final["error"] = e
                await self._record_log(model_name, "error", t0, error=f"{e.code}: {e.message}")
                return

            state = _TurnState()
            try:
                async for frame in self._process_one_turn(upstream_resp, state):
                    yield frame
            except Exception as e:  # pragma: no cover · 上游 SSE 解析中断
                err = ServiceError(
                    status=502,
                    code="upstream_stream_error",
                    message=f"上游流中断: {e}",
                )
                yield self._format_sse_event(
                    "error",
                    {"type": "error", "error": {"type": err.code, "message": err.message}},
                )
                final["error"] = err
                await self._record_log(model_name, "error", t0, error=f"{err.code}: {err.message}")
                return

            # turn 结束:持久化 assistant content
            if conv_repo is not None and conversation_id is not None and state.content:
                await conv_repo.append_message(
                    conversation_id,
                    "assistant",
                    state.content,
                    model_name=model_name,
                )

            # detect tool_use
            tool_uses = self._extract_tool_use_blocks(state.content)
            if not tool_uses:
                # 收敛:这就是最后一轮
                final["msg_id"] = state.msg_id
                final["msg_model"] = state.msg_model
                final["content"] = state.content
                final["stop_reason"] = state.stop_reason
                final["input_tokens"] = state.input_tokens
                final["output_tokens"] = state.output_tokens
                final["content_set"] = True
                await self._record_log(model_name, "ok", t0)
                return

            # 执行所有 tool_use
            tool_result_blocks = await self._execute_tool_uses(tool_uses)

            # 持久化 tool_result(role=user)
            if conv_repo is not None and conversation_id is not None:
                await conv_repo.append_message(
                    conversation_id,
                    "user",
                    tool_result_blocks,
                )

            # 合成 tool_result message,SSE 推给 client
            async for frame in self._emit_synthetic_tool_result_message(tool_result_blocks):
                yield frame

            # 拼下一轮 body
            body_dict.setdefault("messages", []).append(
                {"role": "assistant", "content": state.content},
            )
            body_dict["messages"].append(
                {"role": "user", "content": tool_result_blocks},
            )

        # 出 while → 超 max_iter
        err = ServiceError(
            status=400,
            code="tool_iter_exceeded",
            message=f"工具循环超过 max_iter={max_iter},终止。可调 env CHARIOT_MAX_TOOL_ITER 提高",
        )
        yield self._format_sse_event(
            "error",
            {"type": "error", "error": {"type": err.code, "message": err.message}},
        )
        final["error"] = err
        await self._record_log(model_name, "error", t0, error=f"{err.code}: {err.message}")

    async def _process_one_turn(
        self,
        upstream_resp: Response,
        state: _TurnState,
    ) -> AsyncIterator[bytes]:
        """消费一轮上游 streaming 响应。

        - 转发每个 SSE 帧给 client(re-encode,丢 SSE id / retry 字段;Anthropic 协议
          只有 event 名 + data,re-encode 等价)
        - 边转发边累积 `state`:msg meta、content blocks(文本 / tool_use)
        - 注意 `body_iterator`:`StreamingResponse` 暴露的属性是 `AsyncContentStream`,
          实际 yield 类型可能是 bytes / str / memoryview;SseParser 接 `AsyncIterable[bytes]`,
          我们假设上游 Model 都吐 bytes(MockModel / AnthropicModel 都是)
        """
        body_iter = self._extract_body_iterator(upstream_resp)
        async for event_name, data in SseParser.iter_frames(body_iter):
            etype = event_name or data.get("type")
            if not isinstance(etype, str):
                continue

            # 先 yield 原帧给 client(re-encode)
            yield self._format_sse_event(etype, data)

            # 边收边解析 state
            self._update_turn_state(state, etype, data)

    @staticmethod
    def _extract_body_iterator(resp: Response) -> AsyncIterator[bytes]:
        """从 StreamingResponse 拿 body_iterator 当作 AsyncIterable[bytes]。

        非 StreamingResponse(Response 的非流子类)→ 退化:拿 .body 包成单个
        chunk 的 async iterator。理论上 0.5.0 slow path 永远要 stream=True 给上游,
        所以这是兜底分支。
        """
        if isinstance(resp, StreamingResponse):
            return cast(AsyncIterator[bytes], resp.body_iterator)

        async def _once() -> AsyncIterator[bytes]:
            body = resp.body
            yield bytes(body) if isinstance(body, memoryview) else body

        return _once()

    @staticmethod
    def _update_turn_state(state: _TurnState, etype: str, data: dict[str, Any]) -> None:
        """按 Anthropic SSE 事件更新 _TurnState(msg meta + content blocks)。"""
        if etype == "message_start":
            msg = data.get("message")
            if isinstance(msg, dict):
                m = cast(dict[str, Any], msg)
                state.msg_id = str(m.get("id", "")) or state.msg_id
                state.msg_model = str(m.get("model", "")) or state.msg_model
                state.role = str(m.get("role", "assistant"))
                u = m.get("usage")
                if isinstance(u, dict):
                    ud = cast(dict[str, Any], u)
                    state.input_tokens = int(ud.get("input_tokens", 0) or 0)
                    state.output_tokens = int(ud.get("output_tokens", 0) or 0)
            return

        if etype == "content_block_start":
            idx = int(data.get("index", 0) or 0)
            block = data.get("content_block")
            if isinstance(block, dict):
                state.current_block = dict(cast(dict[str, Any], block))
                state.current_index = idx
                btype = state.current_block.get("type")
                if btype == "text":
                    state.text_buf[idx] = []
                elif btype == "tool_use":
                    state.tool_input_buf[idx] = ""
            return

        if etype == "content_block_delta":
            idx = int(data.get("index", 0) or 0)
            delta = data.get("delta")
            if not isinstance(delta, dict):
                return
            d = cast(dict[str, Any], delta)
            dtype = d.get("type")
            if dtype == "text_delta":
                txt = d.get("text", "")
                if isinstance(txt, str):
                    state.text_buf.setdefault(idx, []).append(txt)
            elif dtype == "input_json_delta":
                pj = d.get("partial_json", "")
                if isinstance(pj, str):
                    state.tool_input_buf[idx] = state.tool_input_buf.get(idx, "") + pj
            return

        if etype == "content_block_stop":
            idx = int(data.get("index", 0) or 0)
            block = state.current_block
            if block is not None and state.current_index == idx:
                btype = block.get("type")
                if btype == "text":
                    block["text"] = "".join(state.text_buf.get(idx, []))
                elif btype == "tool_use":
                    raw = state.tool_input_buf.get(idx, "") or "{}"
                    try:
                        block["input"] = json.loads(raw)
                    except json.JSONDecodeError:
                        block["input"] = {}
                state.content.append(block)
                state.current_block = None
                state.current_index = -1
            return

        if etype == "message_delta":
            delta = data.get("delta")
            if isinstance(delta, dict):
                sr = cast(dict[str, Any], delta).get("stop_reason")
                if isinstance(sr, str):
                    state.stop_reason = sr
            u = data.get("usage")
            if isinstance(u, dict):
                ot = cast(dict[str, Any], u).get("output_tokens")
                if isinstance(ot, int):
                    state.output_tokens = ot
            return

        # message_stop / 其它事件:state 不需要再变,数据已落到 content_block_stop
        return

    async def _emit_synthetic_tool_result_message(
        self,
        tool_result_blocks: list[dict[str, Any]],
    ) -> AsyncIterator[bytes]:
        """合成一条 role=user 的 tool_result message,通过 SSE 推给 client。

        每个 tool_result block 直接整块塞 `content_block_start.content_block`,不
        走 `text_delta`(Anthropic 协议没规定 tool_result 的 streaming 形态,整块
        发送是最稳妥的兼容做法)。message id 用 `msg_chariot_tu_${ulid}` 前缀,
        让客户端能识别"这条不是 LLM 返的,是 chariot 注入的工具结果"。
        """
        msg_id = f"msg_chariot_tu_{ULID()}"
        yield self._format_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "user",
                    "content": [],
                    "model": "chariot",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        for i, block in enumerate(tool_result_blocks):
            yield self._format_sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": block,
                },
            )
            yield self._format_sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": i},
            )
        yield self._format_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 0},
            },
        )
        yield self._format_sse_event("message_stop", {"type": "message_stop"})

    @staticmethod
    def _format_sse_event(name: str, data: dict[str, Any]) -> bytes:
        """格式化一条 named SSE frame(跟 MockModel._sse_event 同款,共享 wire 格式)。"""
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {name}\ndata: {payload}\n\n".encode()

    async def _execute_tool_uses(
        self,
        tool_uses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """跑所有 tool_use,返 tool_result blocks 列表(已带 tool_use_id)。"""
        blocks: list[dict[str, Any]] = []
        for tu in tool_uses:
            tu_id = tu.get("id", "")
            tu_name = tu.get("name", "")
            tu_input = tu.get("input", {})
            if not isinstance(tu_input, dict):
                tu_input = {}
            tool = self.tools.get(tu_name) if isinstance(tu_name, str) else None
            if tool is None:
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": [
                            {"type": "text", "text": f"未知或未启用的工具: {tu_name!r}"},
                        ],
                        "is_error": True,
                    },
                )
                continue
            result = await tool.execute(cast(dict[str, Any], tu_input))
            result["tool_use_id"] = tu_id
            blocks.append(result)
        return blocks

    # ---- 内部:body / response 解析 ----

    @staticmethod
    def _parse_body_dict(body: bytes) -> dict[str, Any]:
        """body → dict;非法 JSON / 顶层非 dict → 返空 dict。"""
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}

    @staticmethod
    def _extract_model_name(body_dict: dict[str, Any]) -> str | None:
        m = body_dict.get("model")
        return m if isinstance(m, str) and m else None

    @staticmethod
    def _detect_stream(body_dict: dict[str, Any]) -> bool:
        return body_dict.get("stream") is True

    @staticmethod
    def _extract_messages(body_dict: dict[str, Any]) -> list[dict[str, Any]]:
        msgs = body_dict.get("messages")
        if not isinstance(msgs, list):
            return []
        return [m for m in cast(list[Any], msgs) if isinstance(m, dict)]

    @staticmethod
    def _extract_tool_use_blocks(
        content: Any,
    ) -> list[dict[str, Any]]:
        """从 assistant content 数组里抽出所有 type='tool_use' blocks。"""
        if not isinstance(content, list):
            return []
        result: list[dict[str, Any]] = []
        for b in cast(list[Any], content):
            if not isinstance(b, dict):
                continue
            block = cast(dict[str, Any], b)
            if block.get("type") == "tool_use":
                result.append(block)
        return result

    @staticmethod
    def _max_tool_iter() -> int:
        """从 env 读 max_iter,默认 10。非法值 fallback 默认。"""
        # pyright stubs 对 os.environ.get 推断不全(reportUnknownMemberType),用 ignore
        raw: str | None = os.environ.get("CHARIOT_MAX_TOOL_ITER")  # pyright: ignore[reportUnknownMemberType]
        if raw is None:
            return _DEFAULT_MAX_TOOL_ITER
        try:
            n = int(raw)
        except ValueError:
            return _DEFAULT_MAX_TOOL_ITER
        return n if n > 0 else _DEFAULT_MAX_TOOL_ITER

    # ---- 日志 ----

    async def _record_log(
        self,
        model: str | None,
        status: str,
        t0: float,
        *,
        error: str | None = None,
    ) -> None:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await log_writer.record(
            model=model,
            status=status,  # type: ignore[arg-type]
            latency_ms=latency_ms,
            error=error,
        )
