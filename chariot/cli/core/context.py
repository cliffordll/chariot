"""chat 会话上下文:客户端 + 会话配置 + 多轮 messages 历史。

`ChatContext` 同时服务一次性命令(`commands/chat.py::_one_shot`)和 REPL(`repl.py`)。
独立于 typer / REPL / 前端 UI,只管"一轮流式请求 + usage 抽取 + 消息历史"。

0.2.0 起 chariot 单协议化(只接 Anthropic Messages),`ChatContext` 跟着收敛 ——
不再持 fmt 字段,_build_body 只产 messages 协议体。

stateful / stateless body.messages 契约(0.4.3 起严格遵守)
-----------------------------------------------------------
- **stateless**(`conversation_id is None`):body.messages = 整段本地历史,server
  不持久化,model 看到的就是 client 传的全部
- **stateful**(`conversation_id is not None`):body.messages = **只发本轮新增 user
  msg**(`self.messages[-1:]`)。server 端 `Agent.handle` 会:
  (a) 从 messages 表 load 历史 prepend 到 body 前面再喂 model
  (b) 把 body.messages 全部 append 到 messages 表
  → 所以 client 必须只送"新增"的部分,否则 server 会把已 persist 的历史再 append
  一遍 → DB 翻倍 + model 看到双份 history。详见 `Agent._run_tool_loop`
  (`chariot/server/agent.py:207-216`)

本地 `self.messages` 仍累积所有轮(给 REPL 失败 `pop_last` 回退用,以及 stateless
模式拼 body 用),但 stateful 模式下 `_build_body` 只取末尾那条 user msg 进 body。

典型用法
--------
```
ctx = ChatContext(client=client, model="claude-haiku-4-5")
ctx.append_user("hi")

def on_event(ev):
    if ev.kind == "text":
        print(ev.text, end="", flush=True)
    elif ev.kind == "tool_use":
        print(f"\\n→ {ev.tool_name}({ev.tool_input})")

result = await ctx.run_turn(on_event)
ctx.append_assistant(result.text)
```
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chariot.sdk.client import ProxyClient
from chariot.sdk.streams import ChatStream, StreamEvent

DEFAULT_MODEL: str = "claude-haiku-4-5"


def _empty_messages() -> list[dict[str, Any]]:
    """messages 字段的 default_factory;helper 函数显式标注类型避免 pyright 报 Unknown。

    content 既可能是 str(本地 append 出的)也可能是 anthropic content blocks 数组
    (server canonical history refresh 回来的),用 Any 覆盖两种形态。
    """
    return []


@dataclass
class ChatError(Exception):
    """上游 4xx / 5xx 时 run_turn 抛出的异常,body 为响应正文。"""

    status: int
    body: str

    def short_body(self, limit: int = 200) -> str:
        s = self.body.strip()
        return s if len(s) <= limit else s[:limit] + "…"


@dataclass(frozen=True)
class TurnResult:
    """一轮流式请求的收尾结果(替代原 tuple[str, int, int, int] 返回)。"""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass
class ChatContext:
    """一次聊天会话的完整上下文:客户端 + 会话配置 + 多轮历史。"""

    client: ProxyClient
    model: str
    max_tokens: int = 1024
    messages: list[dict[str, Any]] = field(default_factory=_empty_messages)
    # 0.4.0:可选 conversation id(ULID)。给了则 SDK 附 X-Chariot-Conversation header,
    # server 走 stateful 路径(load 历史 + persist 这轮)。注意:server-side 持久化
    # 后,本地 self.messages 跟 server 的 history 可能重复,但 server 端约定 client
    # 只传"这一轮新增"。CLI chat 模式下 self.messages 仍累积本进程内的轮(便于
    # REPL 打印 / 撤回),发请求时 server 看到 history + new turn 拼出来一致
    conversation_id: str | None = None

    # ---------- 状态操作 ----------

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def pop_last(self) -> None:
        """撤回最后一条消息;REPL 本轮请求失败时用,避免污染后续上下文。"""
        if self.messages:
            self.messages.pop()

    def reset(self) -> None:
        """清空对话历史,保留会话配置(model / max_tokens)。"""
        self.messages.clear()

    def set_model(self, model: str) -> None:
        self.model = model

    async def refresh_from_server(self) -> None:
        """从 server canonical history 拉 messages 替换本地 self.messages。

        解决跨客户端协调(0.5.0 issue 2):另一个 client(UI / 第二个 CLI session)
        往同一 conversation_id 写后,本地 self.messages 仍是上次发请求时的快照,
        导致 REPL 看不到对方写入。每轮 send 前调一次,把本地视图对齐到 server。

        - **stateless**(`conversation_id is None`):server 没存,直接 no-op
        - **stateful**:GET /admin/conversations/{id} 拿 messages,**整段替换** self.messages
          (不 merge,server 是真源)
        - 网络 / 4xx 错:静默吞 —— refresh 失败不应该阻塞用户发消息;
          下游 `_messages_to_send()` 在 stateful 模式只发末尾那条 user,本地视图
          滞后只影响 `/conversation` 显示的 msg count,实际上行 body 不受影响

        刚 append_user 完了再调?**不要**。调用方应在 `append_user` 之前 refresh,
        否则刚 append 的本轮 user msg 会被 server 历史(还没看到这条)覆盖丢掉。
        """
        if self.conversation_id is None:
            return
        try:
            detail = await self.client.get_conversation(self.conversation_id)
        except Exception:
            # 静默降级,refresh 不应阻塞 send
            return
        # MessageOut.content 已是反序列化后的原形态(str 或 anthropic content blocks)
        self.messages = [{"role": m.role, "content": m.content} for m in detail.messages]

    # ---------- 核心:一轮请求 ----------

    async def run_turn(self, on_event: Callable[[StreamEvent], None]) -> TurnResult:
        """用当前 `self.messages` 发一轮流式请求,`on_event` 实时收每个 typed event。

        Caller 用 `on_event` 分派显示:text 增量逐 token 流;tool_use 单行打印;
        tool_result 单行打印(0.5.0 协议级流式工具循环,一条响应里可能多 turn)。

        TurnResult.text 取的是 **最后一个 assistant turn**(stop_reason 收敛那轮)的
        文本;之前的 assistant turn(后跟 tool_use 那种)文本算"过程文本",不进
        TurnResult —— caller 已经通过 on_event 看到过了。

        server 4xx / 5xx 时抛 `ChatError`(body = 响应正文)。
        """
        body = self._build_body()
        stream = ChatStream()
        # 跨 turn 跟踪:current_turn_text 是当前正在累积的 assistant turn 文本;
        # 每次 turn_complete(role=assistant)snapshot 到 last_assistant_text,
        # 被下一轮覆盖,流尾留下的就是最终轮的文本。
        current_turn_text: list[str] = []
        last_assistant_text: list[str] = []
        t0 = time.monotonic()

        async with self.client.stream_chat(body, conversation_id=self.conversation_id) as resp:
            if resp.status_code >= 400:
                err_bytes = await resp.aread()
                raise ChatError(
                    status=resp.status_code,
                    body=err_bytes.decode("utf-8", errors="replace"),
                )
            async for ev in stream.events(resp):
                on_event(ev)
                if ev.kind == "text":
                    current_turn_text.append(ev.text)
                elif ev.kind == "turn_complete" and ev.role == "assistant":
                    # 这一轮 assistant 收尾(可能是 tool_use 中转,也可能是 final)
                    last_assistant_text = current_turn_text
                    current_turn_text = []

        return TurnResult(
            text="".join(last_assistant_text),
            input_tokens=stream.input_tokens,
            output_tokens=stream.output_tokens,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    # ---------- 私有:组装请求体 ----------

    def _build_body(self) -> dict[str, Any]:
        """把对话历史组装成 Anthropic Messages 请求体。

        body.messages 取值取决于 stateful / stateless;详见模块级 docstring 的契约段。
        """
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": self._messages_to_send(),
        }

    def _messages_to_send(self) -> list[dict[str, Any]]:
        """决定 body.messages 装什么。

        - stateful(有 conversation_id):只发末尾那条(本轮新增 user msg);server
          自己从 DB prepend 历史。这条规则避免 server 重复 persist 老消息(详见
          模块 docstring 的"契约"段)
        - stateless:发全量本地历史,server 不持久化
        """
        if self.conversation_id is not None:
            return self.messages[-1:] if self.messages else []
        return self.messages
