"""`chariot chat` 的终端 REPL 循环。

`ChatRepl` 实例类持有 `ChatContext`,职责:
- 读用户输入(`prompt_toolkit.PromptSession`)
- `/` 开头分派 slash 命令,否则作为新一轮 user message
- 每轮调 `ctx.run_turn()` 流式打印 assistant + meta 行

slash 命令(0.4.0 扩展)
- `/exit`、`/quit`             退出 REPL
- `/reset`                     清空本地对话历史
- `/help`                      列命令
- `/model`                     显示当前模型(只读;不打 server)
- `/model <name>`              切到指定 entry(本地状态,不打 server)
- `/models`                    列出 server 已注册 entries(name / type)
- `/conversation`              显示当前会话(id / model / 本地消息数)
- `/conversation new`          生成新 ULID 并切到 stateful 模式
- `/conversation <ULID>`       接续指定会话
- `/conversation off`          切回 stateless(不再带 X-Chariot-Conversation)
- `/conversations`             列最近会话(id / title / last_model / msg 数)
- `/tool`                      显示当前已启用工具(filter enabled=True)
- `/tools`                     列全部内置工具(含未启用,带 ON / off 标记)

设计:**单数 = 显示当前状态(只读)**,**复数 = 列出全部**。
单数命令只读 ctx / 不打 server(`/model` / `/conversation`)或只展示 enabled
子集(`/tool`),复数走 server list API 全量列。

输入交互(0.4.1)
----------------
用 prompt_toolkit 取代裸 `input()`,跨平台获得:
- ↑ / ↓ 翻历史(持久化到 `~/.chariot/repl_history`,跨 session 沿用)
- Ctrl-R 反向搜索历史
- Tab 触发 slash 命令补全(候选见 `_SLASH_COMMANDS`)
- Ctrl-C / Ctrl-D 仍抛 KeyboardInterrupt / EOFError(语义跟 `input()` 一致)

状态持有
--------
会话状态(model / max_tokens / messages / conversation_id)全部在 `ChatContext`
实例里。本类只负责"输入分派 + 打印"。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from ulid import ULID

from chariot.cli.core.context import ChatContext, ChatError
from chariot.cli.core.render import Renderer

_PROTOCOL_LABEL = "messages"
_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""跟 server `controller/dataplane.py` 里的 `_ULID_RE` 完全一致。"""


@dataclass
class ChatRepl:
    """终端 REPL 会话。持有一个 `ChatContext`,循环读输入并分派命令。"""

    ctx: ChatContext

    # U+203A 单右尖引号,和普通 > 视觉上有区别,便于识别 REPL 提示符
    _PROMPT: ClassVar[str] = "› "  # noqa: RUF001

    _HISTORY_PATH: ClassVar[Path] = Path.home() / ".chariot" / "repl_history"
    """REPL 输入历史文件,跨 session 持久化。位置跟 `endpoint.json` / `chariot.db` 同根。"""

    _SLASH_COMMANDS: ClassVar[list[str]] = [
        "/exit",
        "/quit",
        "/reset",
        "/help",
        "/model",
        "/models",
        "/conversation",
        "/conversations",
        "/tool",
        "/tools",
    ]
    """Tab 补全候选。新增 slash 命令时同步加这里(`_HELP` 文案是另一个真源)。"""

    _HELP: ClassVar[str] = (
        "slash 命令:\n"
        "  /exit, /quit             退出 REPL\n"
        "  /reset                   清空本地对话历史\n"
        "  /model                   显示当前模型\n"
        "  /model <name>            切到指定 entry\n"
        "  /models                  列 server 已注册的模型 entries\n"
        "  /conversation            显示当前会话(id / model / 本地消息数)\n"
        "  /conversation new        生成新 ULID 并切到 stateful 模式\n"
        "  /conversation <ULID>     接续指定会话\n"
        "  /conversation off        切回 stateless\n"
        "  /conversations           列最近会话\n"
        "  /tool                    显示当前已启用工具\n"
        "  /tools                   列全部内置工具(含未启用)\n"
        "  /help                    本说明"
    )

    async def run(self) -> None:
        """主循环:读输入 → 分派 slash / 发请求 → 打印 meta 行。

        Ctrl+C / EOF / `/exit` / `/quit` 退出。
        """
        Renderer.out(
            f"chariot chat · model={self.ctx.model}"
            + (f" · conv={self.ctx.conversation_id}" if self.ctx.conversation_id else "")
            + " · /help 查看命令",
        )
        session = self._make_prompt_session()

        while True:
            try:
                line = await session.prompt_async(self._PROMPT)
            except (EOFError, KeyboardInterrupt):
                Renderer.stream_newline()
                return

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                if await self._handle_slash(line):
                    return
                continue

            await self._one_turn(line)

    def _make_prompt_session(self) -> PromptSession[str]:
        """搭一个 PromptSession:历史持久化 + slash 命令 Tab 补全。

        - 历史文件父目录懒建,跟其它 ~/.chariot/* 资源(endpoint.json / chariot.db)同位
        - WordCompleter 只在 Tab 时触发(`complete_while_typing=False`),不打扰正常打字;
          普通文本 Tab 也会触发但匹配不到任何候选,实际无副作用
        """
        self._HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        return PromptSession(
            history=FileHistory(str(self._HISTORY_PATH)),
            completer=WordCompleter(
                self._SLASH_COMMANDS,
                ignore_case=False,
                sentence=False,
            ),
            complete_while_typing=False,
        )

    async def _one_turn(self, user_text: str) -> None:
        """发一轮请求;失败撤回 user,避免污染后续上下文。

        0.5.0 起 server 可能返多 turn(slow path 工具循环),`Renderer.render_event`
        把 typed StreamEvent 分派为屏幕输出:text 逐 token 流,tool_use / tool_result
        各自单行渲染。
        """
        self.ctx.append_user(user_text)
        try:
            result = await self.ctx.run_turn(Renderer.render_event)
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

    # ---------- slash dispatch ----------

    async def _handle_slash(self, line: str) -> bool:
        """处理 slash 命令;返 True 表示要退出 REPL。

        async 因为列表类命令(`/models` / `/conversations` / `/tool` / `/tools`)走 server API。
        """
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
            self._slash_model(arg)
            return False
        if cmd == "/models":
            await self._slash_models_list(arg)
            return False
        if cmd == "/conversation":
            await self._slash_conversation(arg)
            return False
        if cmd == "/conversations":
            await self._slash_conversations_list(arg)
            return False
        if cmd == "/tool":
            await self._slash_tool_show(arg)
            return False
        if cmd == "/tools":
            await self._slash_tools_list(arg)
            return False

        Renderer.error_bubble(f"未知命令 {cmd!r};/help 查看可用命令")
        return False

    # ---------- /model · /models ----------

    def _slash_model(self, arg: str) -> None:
        """`/model` 显示当前模型;`/model <name>` 切到指定 entry(本地状态,不打 server)。"""
        if not arg:
            Renderer.out(f"model: {self.ctx.model}")
            return
        self.ctx.set_model(arg)
        Renderer.out(f"model → {self.ctx.model}")

    async def _slash_models_list(self, arg: str) -> None:
        """`/models` — 列 server 已注册的模型 entries(name / type)。"""
        if arg:
            Renderer.error_bubble("/models 不接受参数;切模型用 `/model <name>`")
            return
        try:
            data = await self.ctx.client.list_models()
        except httpx.HTTPError as e:
            Renderer.error_bubble(f"列模型失败: {e}")
            return
        if not data.entries:
            Renderer.out("(server 没注册 entry — `chariot model add` 加一条)")
            return
        rows = [
            (e.name, e.type, "← current" if e.name == self.ctx.model else "") for e in data.entries
        ]
        Renderer.table(["name", "type", ""], rows, title="entries")

    # ---------- /conversation · /conversations ----------

    async def _slash_conversation(self, arg: str) -> None:
        """`/conversation` 显示当前会话;`/conversation new` 新建;`/conversation <ULID>` 切;
        `/conversation off` 切回 stateless。

        切会话会清空本地 self.messages —— server 端是该 ULID 历史的真源,本地累积
        的 messages 属上一会话,留着会让下一轮请求把上一会话的旧 turn 重新塞过去。
        """
        if not arg:
            self._show_current_conversation()
            return

        if arg.lower() == "off":
            self.ctx.conversation_id = None
            self.ctx.reset()
            Renderer.out("conversation → off (stateless)")
            return

        if arg.lower() == "new":
            new_id = str(ULID())
            self.ctx.conversation_id = new_id
            self.ctx.reset()
            Renderer.out(f"conversation → {new_id} (new)")
            return

        if not _ULID_RE.match(arg):
            Renderer.error_bubble(
                f"非法 conversation 取值: {arg!r};应为 'new' / 'off' / 26 字符 ULID",
            )
            return

        self.ctx.conversation_id = arg
        self.ctx.reset()
        Renderer.out(f"conversation → {arg}")

    def _show_current_conversation(self) -> None:
        """`/conversation` 无参数:打印当前会话快照(只读 ctx,不打 server)。"""
        if self.ctx.conversation_id is None:
            Renderer.out(f"conversation: off (stateless) · model={self.ctx.model}")
            return
        Renderer.out(
            f"conversation: {self.ctx.conversation_id} · "
            f"model={self.ctx.model} · "
            f"local msgs={len(self.ctx.messages)}",
        )

    async def _slash_conversations_list(self, arg: str) -> None:
        """`/conversations` — 列最近 20 条会话。"""
        if arg:
            Renderer.error_bubble(
                "/conversations 不接受参数;切会话用 `/conversation <ULID|new|off>`",
            )
            return
        try:
            resp = await self.ctx.client.list_conversations(limit=20)
        except httpx.HTTPError as e:
            Renderer.error_bubble(f"列会话失败: {e}")
            return
        if not resp.items:
            Renderer.out("(没有会话 — `/conversation new` 开一个)")
            return
        rows = [
            (
                it.id,
                _truncate(it.title or "(无标题)", 30),
                it.last_model or "-",
                str(it.message_count),
                "← current" if it.id == self.ctx.conversation_id else "",
            )
            for it in resp.items
        ]
        Renderer.table(
            ["id", "title", "last_model", "msgs", ""],
            rows,
            title="conversations",
        )

    # ---------- /tool · /tools ----------

    async def _slash_tool_show(self, arg: str) -> None:
        """`/tool` — 显示当前已启用工具(filter enabled=True)。

        改启用状态用 `chariot tool enable|disable|config <name>` 或 GUI Tools 页。
        """
        if arg:
            Renderer.error_bubble(
                "/tool 不接受参数;改启用 / options 用 "
                "`chariot tool enable|disable|config <name>` 或 GUI Tools 页",
            )
            return
        try:
            data = await self.ctx.client.list_tools()
        except httpx.HTTPError as e:
            Renderer.error_bubble(f"列工具失败: {e}")
            return
        enabled = [t for t in data.entries if t.enabled]
        if not enabled:
            Renderer.out("(没有已启用的工具 — `chariot tool enable <name>` 启用一个)")
            return
        rows = [
            (
                t.name,
                t.type,
                _truncate(json.dumps(t.options, ensure_ascii=False), 40),
            )
            for t in enabled
        ]
        Renderer.table(["name", "type", "options"], rows, title="enabled tools")

    async def _slash_tools_list(self, arg: str) -> None:
        """`/tools` — 列全部内置工具(含未启用,带 ON / off 标记)。"""
        if arg:
            Renderer.error_bubble(
                "/tools 不接受参数;启用 / 关闭工具用 `chariot tool enable|disable <name>`",
            )
            return
        try:
            data = await self.ctx.client.list_tools()
        except httpx.HTTPError as e:
            Renderer.error_bubble(f"列工具失败: {e}")
            return
        if not data.entries:
            Renderer.out("(server 没注册工具)")
            return
        rows = [
            (
                t.name,
                t.type,
                "ON" if t.enabled else "off",
                _truncate(json.dumps(t.options, ensure_ascii=False), 40),
            )
            for t in data.entries
        ]
        Renderer.table(["name", "type", "enabled", "options"], rows, title="tools")


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"
