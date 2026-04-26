"""`chariot chat` 的终端 REPL 循环。

`ChatRepl` 实例类持有 `ChatContext`,职责:
- 读用户输入(`input()`)
- `/` 开头分派 slash 命令,否则作为新一轮 user message
- 每轮调 `ctx.run_turn()` 流式打印 assistant + meta 行
- slash 命令:`/exit`(`/quit` 别名) / `/reset` / `/model <name>` / `/help`

状态持有
--------
会话状态(model / max_tokens / messages)全部在 `ChatContext` 实例里。
本类只负责"输入分派 + 打印"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from chariot.cli.core.context import ChatContext, ChatError
from chariot.cli.core.render import Renderer

_PROTOCOL_LABEL = "messages"


@dataclass
class ChatRepl:
    """终端 REPL 会话。持有一个 `ChatContext`,循环读输入并分派命令。"""

    ctx: ChatContext

    # U+203A 单右尖引号,和普通 > 视觉上有区别,便于识别 REPL 提示符
    _PROMPT: ClassVar[str] = "› "  # noqa: RUF001

    _HELP: ClassVar[str] = (
        "slash 命令:\n"
        "  /exit, /quit             退出 REPL\n"
        "  /reset                   清空对话历史\n"
        "  /model <name>            切换模型\n"
        "  /help                    本说明"
    )

    async def run(self) -> None:
        """主循环:读输入 → 分派 slash / 发请求 → 打印 meta 行。

        Ctrl+C / EOF / `/exit` / `/quit` 退出。
        """
        Renderer.out(f"chariot chat · model={self.ctx.model} · /help 查看命令")

        while True:
            try:
                line = input(self._PROMPT)
            except (EOFError, KeyboardInterrupt):
                Renderer.stream_newline()
                return

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                if self._handle_slash(line):
                    return
                continue

            await self._one_turn(line)

    async def _one_turn(self, user_text: str) -> None:
        """发一轮请求;失败撤回 user,避免污染后续上下文。"""
        self.ctx.append_user(user_text)
        try:
            result = await self.ctx.run_turn(Renderer.stream_token)
        except ChatError as e:
            Renderer.stream_newline()
            self.ctx.pop_last()
            Renderer.error_bubble(f"HTTP {e.status}: {e.short_body()}")
            return

        Renderer.stream_newline()
        self.ctx.append_assistant(result.text)
        Renderer.meta_line(
            model=self.ctx.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            path=_PROTOCOL_LABEL,
        )

    def _handle_slash(self, line: str) -> bool:
        """处理 slash 命令;返回 True 表示要退出 REPL。"""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            return True

        if cmd == "/help":
            Renderer.out(self._HELP)
            return False

        if cmd == "/reset":
            self.ctx.reset()
            Renderer.out("history cleared")
            return False

        if cmd == "/model":
            if not arg:
                Renderer.error_bubble("用法:/model <name>")
                return False
            self.ctx.set_model(arg)
            Renderer.out(f"model → {self.ctx.model}")
            return False

        Renderer.error_bubble(f"未知命令 {cmd!r};/help 查看可用命令")
        return False
