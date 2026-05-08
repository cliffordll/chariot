"""`chariot chat` 的终端 REPL 循环。

`ChatRepl` 实例类持有 `ChatContext`,职责:
- 读用户输入(`prompt_toolkit.PromptSession`)
- `/` 开头分派 slash 命令,否则作为新一轮 user message
- 每轮调 `ctx.run_turn()` 流式打印 assistant + meta 行

slash 命令(0.6.0 沿用,0.6.0 起 model→provider rename)
- `/exit`、`/quit`             退出 REPL
- `/reset`                     清空本地对话历史
- `/help`                      列命令
- `/provider`                  显示当前 provider(只读)
- `/provider <name>`           切到指定 entry(本地状态;实际写到 ChatRequest.model)
- `/providers`                 列出已注册 provider entries(name / type)
- `/convo`                     显示当前会话(id / provider / 本地消息数)
- `/convo new`                 生成新 ULID 并切到 stateful 模式
- `/convo <ULID>`              接续指定会话
- `/convo off`                 切回 stateless
- `/convos`                    列最近会话(id / title / last_model / msg 数)
- `/tool`                      显示当前已启用工具(filter enabled=True)
- `/tools`                     列全部内置工具(含未启用,带 ON / off 标记)

设计:**单数 = 显示当前状态(只读)**,**复数 = 列出全部**。
单数命令只读 ctx / 不查 DB(`/provider` / `/convo`)或只展示 enabled
子集(`/tool`),复数走 repo list 全量。

**关于 `/provider` vs `chariot chat --model`**:CLI flag 保留 `--model` 跟 Claude
API 的 `body.model` 字段名对齐;REPL slash 改 `/provider` 跟 chariot 内部
`provider entry` 表 / `BaseProvider` 抽象一致(用户切的是哪个 entry,语义是
"provider")。两者底下都写到 `ctx.model`,只是命名 surface 不同。

输入交互
--------
prompt_toolkit:
- ↑ / ↓ 翻历史(持久化到 `~/.chariot/repl_history`,跨 session 沿用)
- Ctrl-R 反向搜索历史
- Tab 触发 slash 命令补全(候选见 `_SLASH_COMMANDS`)
- Ctrl-C / Ctrl-D 抛 KeyboardInterrupt / EOFError(语义跟 `input()` 一致)

状态持有
--------
会话状态(model / max_tokens / messages / convo_id)全部在 `ChatContext`
实例里;repo 调用走 `ctx.agent.session_maker`。本类只负责"输入分派 + 打印"。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from ulid import ULID

from chariot.cli.context import ChatContext, ChatError
from chariot.cli.render import Renderer
from chariot.repos.convo_repo import ConvoRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.repos.tool_repo import ToolRepo

_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""ULID 26 字符;偏宽:Crockford base32 严格排除 I / L / O / U,但 chariot 整体不收紧。"""


@dataclass
class ChatRepl:
    """终端 REPL 会话。持有一个 `ChatContext`,循环读输入并分派命令。"""

    ctx: ChatContext

    # U+203A 单右尖引号,和普通 > 视觉上有区别,便于识别 REPL 提示符
    _PROMPT: ClassVar[str] = "› "  # noqa: RUF001

    _HISTORY_PATH: ClassVar[Path] = Path.home() / ".chariot" / "repl_history"
    """REPL 输入历史文件,跨 session 持久化。位置跟 `chariot.db` 同根。"""

    _SLASH_COMMANDS: ClassVar[list[str]] = [
        "/exit",
        "/quit",
        "/reset",
        "/help",
        "/provider",
        "/providers",
        "/convo",
        "/convos",
        "/tool",
        "/tools",
    ]
    """Tab 补全候选。新增 slash 命令时同步加这里(`_HELP` 文案是另一个真源)。"""

    _HELP: ClassVar[str] = (
        "slash 命令:\n"
        "  /exit, /quit             退出 REPL\n"
        "  /reset                   清空本地对话历史\n"
        "  /provider                显示当前 provider\n"
        "  /provider <name>         切到指定 entry\n"
        "  /providers               列已注册的 provider entries\n"
        "  /convo                   显示当前会话(id / provider / 本地消息数)\n"
        "  /convo new               生成新 ULID 并切到 stateful 模式\n"
        "  /convo <ULID>            接续指定会话\n"
        "  /convo off               切回 stateless\n"
        "  /convos                  列最近会话\n"
        "  /tool                    显示当前已启用工具\n"
        "  /tools                   列全部内置工具(含未启用)\n"
        "  /help                    本说明"
    )

    async def run(self) -> None:
        """主循环:读输入 → 分派 slash / 发请求 → 打印 meta 行。

        Ctrl+C / EOF / `/exit` / `/quit` 退出。
        """
        Renderer.out(
            f"chariot chat · provider={self.ctx.model}"
            + (f" · convo={self.ctx.convo_id}" if self.ctx.convo_id else "")
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
        """搭一个 PromptSession:历史持久化 + slash 命令 Tab 补全。"""
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

        AIAgent 可能产多 message(stop_reason=tool_use 中转 + 最终 end_turn);
        Renderer.render_event 把 ChatEvent 分派为屏幕输出。
        """
        self.ctx.append_user(user_text)
        try:
            result = await self.ctx.run_turn(Renderer.render_event)
        except ChatError as e:
            Renderer.stream_newline()
            self.ctx.pop_last()
            Renderer.error_bubble(f"{e.error_type}: {e.short_message()}")
            return

        Renderer.stream_newline()
        self.ctx.append_assistant(result.text)
        Renderer.meta_line(
            model=self.ctx.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
        )

    # ---------- slash dispatch ----------

    async def _handle_slash(self, line: str) -> bool:
        """处理 slash 命令;返 True 表示要退出 REPL。

        async 因为列表类命令(`/models` / `/conversations` / `/tool` / `/tools`)走 repo。
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
        if cmd == "/provider":
            self._slash_provider(arg)
            return False
        if cmd == "/providers":
            await self._slash_providers_list(arg)
            return False
        if cmd == "/convo":
            await self._slash_convo(arg)
            return False
        if cmd == "/convos":
            await self._slash_convos_list(arg)
            return False
        if cmd == "/tool":
            await self._slash_tool_show(arg)
            return False
        if cmd == "/tools":
            await self._slash_tools_list(arg)
            return False

        Renderer.error_bubble(f"未知命令 {cmd!r};/help 查看可用命令")
        return False

    # ---------- /provider · /providers ----------

    def _slash_provider(self, arg: str) -> None:
        """`/provider` 显示当前 provider;`/provider <name>` 切到指定 entry(本地状态)。

        实际写到 `ctx.model`(对应 ChatRequest.model 字段);命名上叫 provider
        是对齐 chariot 的 `provider entry` 语义。
        """
        if not arg:
            Renderer.out(f"provider: {self.ctx.model}")
            return
        self.ctx.set_model(arg)
        Renderer.out(f"provider → {self.ctx.model}")

    async def _slash_providers_list(self, arg: str) -> None:
        """`/providers` — 列已注册的 provider entries(name / type)。"""
        if arg:
            Renderer.error_bubble(
                "/providers 不接受参数;切 provider 用 `/provider <name>`",
            )
            return
        async with self.ctx.agent.session_maker() as session:
            entries = await ProviderRepo(session).list_entries()
        if not entries:
            Renderer.out("(没有 entry — `chariot provider add` 加一条)")
            return
        rows = [(e.name, e.type, "← current" if e.name == self.ctx.model else "") for e in entries]
        Renderer.table(["name", "type", ""], rows, title="entries")

    # ---------- /convo · /convos ----------

    async def _slash_convo(self, arg: str) -> None:
        """`/convo` 显示当前会话;`/convo new` 新建;
        `/convo <ULID>` 切;`/convo off` 切回 stateless。

        切会话会清空本地 self.messages —— DB 是该 ULID 历史的真源,本地累积
        的属上一会话,留着会让下一轮请求把上一会话的旧 turn 重新塞过去。
        """
        if not arg:
            self._show_current_convo()
            return

        if arg.lower() == "off":
            self.ctx.convo_id = None
            self.ctx.reset()
            Renderer.out("convo → off (stateless)")
            return

        if arg.lower() == "new":
            new_id = str(ULID())
            self.ctx.convo_id = new_id
            self.ctx.reset()
            Renderer.out(f"convo → {new_id} (new)")
            return

        if not _ULID_RE.match(arg):
            Renderer.error_bubble(
                f"非法 convo 取值: {arg!r};应为 'new' / 'off' / 26 字符 ULID",
            )
            return

        self.ctx.convo_id = arg
        self.ctx.reset()
        Renderer.out(f"convo → {arg}")

    def _show_current_convo(self) -> None:
        """`/convo` 无参数:打印当前会话快照(只读 ctx)。"""
        if self.ctx.convo_id is None:
            Renderer.out(f"convo: off (stateless) · provider={self.ctx.model}")
            return
        Renderer.out(
            f"convo: {self.ctx.convo_id} · "
            f"provider={self.ctx.model} · "
            f"local msgs={len(self.ctx.messages)}",
        )

    async def _slash_convos_list(self, arg: str) -> None:
        """`/convos` — 列最近 20 条会话。"""
        if arg:
            Renderer.error_bubble(
                "/convos 不接受参数;切会话用 `/convo <ULID|new|off>`",
            )
            return
        async with self.ctx.agent.session_maker() as session:
            convos = await ConvoRepo(session).list_entries(limit=20)
        if not convos:
            Renderer.out("(没有会话 — `/convo new` 开一个)")
            return
        rows = [
            (
                c.id,
                _truncate(c.title or "(无标题)", 30),
                c.last_model or "-",
                str(c.message_count),
                "← current" if c.id == self.ctx.convo_id else "",
            )
            for c in convos
        ]
        Renderer.table(
            ["id", "title", "last_model", "msgs", ""],
            rows,
            title="convos",
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
        async with self.ctx.agent.session_maker() as session:
            entries = await ToolRepo(session).list_enabled()
        if not entries:
            Renderer.out("(没有已启用的工具 — `chariot tool enable <name>` 启用一个)")
            return
        rows = [
            (
                t.name,
                t.type,
                _truncate(json.dumps(t.options, ensure_ascii=False), 40),
            )
            for t in entries
        ]
        Renderer.table(["name", "type", "options"], rows, title="enabled tools")

    async def _slash_tools_list(self, arg: str) -> None:
        """`/tools` — 列全部内置工具(含未启用,带 ON / off 标记)。"""
        if arg:
            Renderer.error_bubble(
                "/tools 不接受参数;启用 / 关闭工具用 `chariot tool enable|disable <name>`",
            )
            return
        async with self.ctx.agent.session_maker() as session:
            entries = await ToolRepo(session).list_entries()
        if not entries:
            Renderer.out("(没有注册工具)")
            return
        rows = [
            (
                t.name,
                t.type,
                "ON" if t.enabled else "off",
                _truncate(json.dumps(t.options, ensure_ascii=False), 40),
            )
            for t in entries
        ]
        Renderer.table(["name", "type", "enabled", "options"], rows, title="tools")


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"
