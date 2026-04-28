"""Agent — chariot 智能体主类。

职责(0.4.0 多轮记忆 + 工具调用)
--------------------------------
1. 持有 `name → Model` 实例字典(从 DB entries 构造)+ `name → Tool` 实例字典
2. **fast path**(无 tools + 无 conversation):0.3.1 行为,按 body.model 路由,
   单次 `model.respond(body)` 透传
3. **slow path**(有 tools 或 有 conversation_id):
   - 可选 load 历史 messages,prepend 到 body.messages
   - 工具循环:while 检测 tool_use → 执行 → append tool_result → 重调 Model
   - 收敛后若 client 要 stream,重发 model.respond(stream=True) 拿 SSE 形态
4. 每次 handle 落一条 `logs` 记录(latency 是整个 handle 的 total time)

`install_from_config` 是幂等的全量重置 —— 既是首次安装,也是配置变更后的重建。

工具循环 max_iter 通过 env `CHARIOT_MAX_TOOL_ITER` 配置,默认 10;超限抛 400。

stream + slow path 的代价:LLM 多调一次(收敛轮重发拿 stream)。0.5+ 可优化为
"流式 tool_use 检测"。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fastapi.responses import Response

from chariot.server.config import ChariotConfig, ToolConfig
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.repository.conversation_repo import ConversationRepo
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.log_writer import log_writer
from chariot.server.tool.base import Tool
from chariot.server.tool.registry import ToolRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("chariot.server.agent")

_DEFAULT_MAX_TOOL_ITER = 10


class Agent:
    """chariot 智能体。0.4.0 起持有 Model 字典 + Tool 字典 + 工具循环主流程。"""

    # ---- 类级单例 ----

    _current: ClassVar[Agent | None] = None

    # ---- 实例构造 ----

    def __init__(self) -> None:
        self.models: dict[str, Model] = {}
        self.tools: dict[str, Tool] = {}

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
        - 工具循环超限 → 400 tool_iter_exceeded
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

        # Slow path:工具循环 + 可选持久化
        try:
            return await self._run_tool_loop(
                body_dict=body_dict,
                model=model,
                model_name=model_name,
                is_stream=is_stream,
                session=session if has_conv else None,
                conversation_id=conversation_id if has_conv else None,
            )
        except ServiceError as e:
            await self._record_log(model_name, "error", t0, error=f"{e.code}: {e.message}")
            raise
        except Exception as e:  # pragma: no cover
            await self._record_log(model_name, "error", t0, error=str(e))
            raise
        finally:
            # _run_tool_loop 内成功路径不写 log(单一出口在这里)。但若已抛 ServiceError
            # 上面 except 已经写过,这里就别再写;用一个标志判断。简化:成功路径在
            # _run_tool_loop 末尾自己写 ok 日志,失败由 handle 写。
            pass

    # ---- Slow path:工具循环主流程 ----

    async def _run_tool_loop(
        self,
        *,
        body_dict: dict[str, Any],
        model: Model,
        model_name: str,
        is_stream: bool,
        session: AsyncSession | None,
        conversation_id: str | None,
    ) -> Response:
        t0 = time.monotonic()
        conv_repo = (
            ConversationRepo(session)
            if (session is not None and conversation_id is not None)
            else None
        )

        # Stateful 起步:确保 conversation 存在 + load history + persist client 这次发的新 messages
        if conv_repo is not None and conversation_id is not None:
            await conv_repo.ensure_exists(conversation_id)
            history = await conv_repo.load_messages_as_anthropic(conversation_id)
            new_msgs = self._extract_messages(body_dict)
            # 持久化客户端这轮 *新增* 的 messages —— 约定:client 给 conversation_id 时
            # 只传新增 messages(通常 1 条 user),server 自己 prepend 历史。
            for msg in new_msgs:
                role = msg.get("role")
                content = msg.get("content")
                if not isinstance(role, str) or content is None:
                    continue
                await conv_repo.append_message(conversation_id, role, content)
            body_dict["messages"] = list(history) + list(new_msgs)

        max_iter = self._max_tool_iter()
        iter_count = 0
        last_resp: Response | None = None

        while iter_count < max_iter:
            iter_count += 1
            body_bytes = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
            resp = await model.respond(body_bytes, stream=False)
            last_resp = resp

            resp_data = self._parse_response_body(resp)
            assistant_content = resp_data.get("content", [])
            if conv_repo is not None and conversation_id is not None:
                await conv_repo.append_message(
                    conversation_id,
                    "assistant",
                    assistant_content,
                    model_name=model_name,
                )

            tool_uses = self._extract_tool_use_blocks(assistant_content)
            if not tool_uses:
                # 收敛:这就是最后一轮
                await self._record_log(model_name, "ok", t0)
                if is_stream:
                    # 重发拿 stream 形态(简化:LLM 多调一次)
                    return await model.respond(body_bytes, stream=True)
                return resp

            # 执行所有 tool_use,拼下一轮 body
            tool_result_blocks = await self._execute_tool_uses(tool_uses)
            if conv_repo is not None and conversation_id is not None:
                await conv_repo.append_message(
                    conversation_id,
                    "user",
                    tool_result_blocks,
                )
            body_dict.setdefault("messages", []).append(
                {"role": "assistant", "content": assistant_content},
            )
            body_dict["messages"].append(
                {"role": "user", "content": tool_result_blocks},
            )

        del last_resp  # 已超限,丢弃最后一次响应
        raise ServiceError(
            status=400,
            code="tool_iter_exceeded",
            message=f"工具循环超过 max_iter={max_iter},终止。可调 env CHARIOT_MAX_TOOL_ITER 提高",
        )

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
    def _parse_response_body(resp: Response) -> dict[str, Any]:
        """slow path 中间轮的 stream=False Response → JSON dict。失败抛 ServiceError。"""
        body = resp.body
        # starlette Response.body 注解是 bytes | memoryview[int];
        # json.loads 接 str / bytes / bytearray,memoryview 要先转 bytes
        body_bytes = bytes(body) if isinstance(body, memoryview) else body
        try:
            data: Any = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as e:
            raise ServiceError(
                status=502,
                code="upstream_invalid_response",
                message=f"上游返回非法 JSON: {e}",
            ) from e
        if not isinstance(data, dict):
            raise ServiceError(
                status=502,
                code="upstream_invalid_response",
                message="上游响应顶层不是 object",
            )
        return cast(dict[str, Any], data)

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
