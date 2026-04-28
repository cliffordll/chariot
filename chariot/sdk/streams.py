"""SDK 侧 SSE → typed events + usage 累加(Anthropic Messages 协议)。

分层
----
- `SseParser`(0.5.0 起搬到 `chariot/shared/sse.py`):协议无关 SSE 字节流解析器,
  本模块从那里 re-export 维持 0.4.x 的 import 路径不变(`from chariot.sdk
  import SseParser` 仍可用)
- `StreamEvent`:typed 事件 dataclass,`events()` 吐出 —— 给 CLI / GUI 高层渲染
- `ChatStream`:有状态消费器,把 SseParser 吐的原始 SSE 帧翻成 typed events,
  同时累加跨 turn 的 input/output tokens(0.5.0 起 server 可能在一条响应里
  串多个 message_start ... message_stop 块,usage 跨 turn 累加才反映总成本)

历史
----
0.2.0 起单协议化(只接 Anthropic Messages),`ChatStream` 直接内联事件处理;
0.4.x 之前只有 `text_deltas` 一个出口,`text_deltas` 现作为 `events` 的兼容
wrapper(`ev.kind == "text"` 时 yield text)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx

from chariot.shared.sse import SseParser

__all__ = ["ChatStream", "SseParser", "StreamEvent"]


@dataclass(frozen=True)
class StreamEvent:
    """ChatStream.events() 吐的 typed event;`kind` 决定哪些字段有意义。

    Caller 用法::

        async for ev in stream.events(resp):
            if ev.kind == "text":
                print(ev.text, end="")
            elif ev.kind == "tool_use":
                print(f"\\n→ {ev.tool_name}({ev.tool_input!r})\\n")
            elif ev.kind == "tool_result":
                marker = "← error" if ev.is_error else "←"
                print(f"\\n{marker} {ev.tool_result_content}\\n")
            elif ev.kind == "turn_complete":
                pass  # 一轮 LLM 输出完成,可选用 ev.role / ev.input_tokens 等
            elif ev.kind == "stream_done":
                # 整个 HTTP 响应结束,ev.input_tokens / ev.output_tokens 是跨 turn 总和
                pass

    每个 kind 的字段约定:

    - `text`:`text`(单条 text_delta 增量,逐 token 触发)
    - `tool_use`:`tool_use_id`, `tool_name`, `tool_input`(`content_block_stop`
      时一次性触发,`tool_input` 是累积 input_json_delta + JSON 解析后的 dict)
    - `tool_result`:`tool_use_id`, `tool_result_content`(把 content blocks 里
      所有 text 拼起来的字符串),`is_error`(server 合成的 tool_result message
      里 user content block 触发)
    - `turn_complete`:`role`(`assistant` / `user`),`input_tokens` / `output_tokens`
      为本轮(单个 message_start ... message_stop)的 usage
    - `stream_done`:整个 HTTP SSE 流结束,`input_tokens` / `output_tokens` 是
      ChatStream 实例累计的所有 assistant turn 总和(synthetic user-role tool_result
      turn 不计入)
    """

    kind: Literal["text", "tool_use", "tool_result", "turn_complete", "stream_done"]
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    tool_result_content: str = ""
    is_error: bool = False
    role: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatStream:
    """流式文本 + 跨 turn usage 累加 + typed events 派发(Anthropic Messages 协议)。

    用法(0.5.0 推荐)::

        stream = ChatStream()
        async for ev in stream.events(resp):
            ...  # 按 ev.kind 分派

    用法(向后兼容)::

        stream = ChatStream()
        async for tok in stream.text_deltas(resp):
            print(tok, end="", flush=True)
        # 流结束后 stream.input_tokens / stream.output_tokens 可用

    跨 turn 累加规则:server 在一条响应里串多个 `message_start ... message_stop`
    块时,只对 `role=assistant` 的 turn 累加 usage;server 合成的 `role=user`
    tool_result message 不计入(它的 usage 都是 0)。
    """

    input_tokens: int = 0
    output_tokens: int = 0

    async def events(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        """主出口:消费 httpx streaming Response,yield typed StreamEvent 序列。

        实现细节:
        - text_delta 边到边吐 `text` event(逐 token 流式)
        - tool_use / tool_result block 在 `content_block_stop` 时一次性吐(累积
          完成才能拿到完整 input / 完整 result content)
        - message_stop 吐 `turn_complete`(role + 本轮 usage)
        - 整流结束吐 `stream_done`(累计总 usage)
        """
        # ---- 跨 SSE 帧的累积状态 ----
        current_role = "assistant"
        turn_input_tokens = 0
        turn_output_tokens = 0
        # 单 content block 的累积(同一时间只在一个 block 里,index 不重叠到混淆)
        in_tool_use = False
        tu_id = ""
        tu_name = ""
        tu_input_buf = ""
        in_tool_result = False
        tr_id = ""
        tr_content_buf = ""
        tr_is_error = False

        async for event_name, data in SseParser.iter_frames(resp.aiter_bytes()):
            etype = event_name or data.get("type")
            if not isinstance(etype, str):
                continue

            if etype == "message_start":
                msg = data.get("message")
                if isinstance(msg, dict):
                    m = cast(dict[str, Any], msg)
                    role = m.get("role", "assistant")
                    current_role = role if isinstance(role, str) else "assistant"
                    u = m.get("usage")
                    if isinstance(u, dict):
                        ud = cast(dict[str, Any], u)
                        turn_input_tokens = int(ud.get("input_tokens", 0) or 0)
                        turn_output_tokens = int(ud.get("output_tokens", 0) or 0)
                    else:
                        turn_input_tokens = 0
                        turn_output_tokens = 0
                continue

            if etype == "content_block_start":
                block = data.get("content_block")
                if isinstance(block, dict):
                    b = cast(dict[str, Any], block)
                    btype = b.get("type")
                    if btype == "tool_use":
                        in_tool_use = True
                        tu_id = str(b.get("id", "") or "")
                        tu_name = str(b.get("name", "") or "")
                        tu_input_buf = ""
                    elif btype == "tool_result":
                        in_tool_result = True
                        tr_id = str(b.get("tool_use_id", "") or "")
                        tr_content_buf = self._extract_tool_result_text(b.get("content"))
                        tr_is_error = bool(b.get("is_error", False))
                continue

            if etype == "content_block_delta":
                delta = data.get("delta")
                if not isinstance(delta, dict):
                    continue
                d = cast(dict[str, Any], delta)
                dtype = d.get("type")
                if dtype == "text_delta":
                    text = d.get("text", "")
                    if isinstance(text, str) and text:
                        yield StreamEvent(kind="text", text=text)
                elif dtype == "input_json_delta" and in_tool_use:
                    pj = d.get("partial_json", "")
                    if isinstance(pj, str):
                        tu_input_buf += pj
                continue

            if etype == "content_block_stop":
                if in_tool_use:
                    try:
                        parsed_input = json.loads(tu_input_buf or "{}")
                    except json.JSONDecodeError:
                        parsed_input = {}
                    if not isinstance(parsed_input, dict):
                        parsed_input = {}
                    yield StreamEvent(
                        kind="tool_use",
                        tool_use_id=tu_id,
                        tool_name=tu_name,
                        tool_input=cast(dict[str, Any], parsed_input),
                    )
                    in_tool_use = False
                    tu_id = ""
                    tu_name = ""
                    tu_input_buf = ""
                elif in_tool_result:
                    yield StreamEvent(
                        kind="tool_result",
                        tool_use_id=tr_id,
                        tool_result_content=tr_content_buf,
                        is_error=tr_is_error,
                    )
                    in_tool_result = False
                    tr_id = ""
                    tr_content_buf = ""
                    tr_is_error = False
                # text block stop:不发额外 event(text_delta 已经逐增量吐了)
                continue

            if etype == "message_delta":
                u = data.get("usage")
                if isinstance(u, dict):
                    ud = cast(dict[str, Any], u)
                    ot = ud.get("output_tokens")
                    if isinstance(ot, int):
                        # Anthropic message_delta.usage.output_tokens 是累计值
                        turn_output_tokens = ot
                continue

            if etype == "message_stop":
                yield StreamEvent(
                    kind="turn_complete",
                    role=current_role,
                    input_tokens=turn_input_tokens,
                    output_tokens=turn_output_tokens,
                )
                # 累加到 stream 级总和:仅 assistant turn(synthetic user 的
                # tool_result message usage 都是 0,加不加都不影响,但严格不算)
                if current_role == "assistant":
                    self.input_tokens += turn_input_tokens
                    self.output_tokens += turn_output_tokens
                turn_input_tokens = 0
                turn_output_tokens = 0
                continue

            # 其它事件(error / 自定义)不 yield;caller 看不到 turn_complete 即可推断异常
            continue

        yield StreamEvent(
            kind="stream_done",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )

    async def text_deltas(self, resp: httpx.Response) -> AsyncIterator[str]:
        """0.4.x 兼容入口:只吐 text 增量。等价 `events()` 过滤 kind=='text'。"""
        async for ev in self.events(resp):
            if ev.kind == "text" and ev.text:
                yield ev.text

    @staticmethod
    def _extract_tool_result_text(content: Any) -> str:
        """从 tool_result.content 抽出文本表示。

        Anthropic 协议允许 content 是 str 或 list[block]。block 通常 {"type":
        "text", "text": "..."},也允许 image / 其它,但工具结果实际场景文本占绝对多数。
        多个 text block → concat。
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for b in cast(list[Any], content):
                if not isinstance(b, dict):
                    continue
                bd = cast(dict[str, Any], b)
                if bd.get("type") == "text":
                    text = bd.get("text", "")
                    if isinstance(text, str):
                        texts.append(text)
            return "".join(texts)
        return ""
