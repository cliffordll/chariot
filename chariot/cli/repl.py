"""`chariot chat` 的终端 REPL 循环。

`ChatRepl` 实例类持有 `ChatContext`,职责:
- 读用户输入(`prompt_toolkit.PromptSession`)
- `/` 开头分派 slash 命令,否则作为新一轮 user message
- 每轮调 `ctx.run_turn()` 流式打印 assistant + meta 行

slash 命令(v6 起 model→provider rename;v7 起加默认 provider)
- `/exit`、`/quit`             退出 REPL
- `/reset`                     清空本地对话历史
- `/help`                      列命令
- `/provider`                  显示当前 provider(只读)
- `/provider <name>`           本次会话切到指定 entry(本地状态,不动 DB 默认)
- `/provider use <name>`       把 <name> 设为 DB 默认(等价 `chariot provider use`)
- `/providers`                 列出已注册 provider entries(name / type / default 标记)
- `/conversation`                     显示当前会话(id / provider / 本地消息数)
- `/conversation new`                 生成新 ULID 并切到 stateful 模式
- `/conversation <ULID>`              接续指定会话
- `/conversation off`                 切回 stateless
 - `/conversations`                    列最近会话(id / title / agent / msg 数)
- `/tool`                      显示当前已启用工具(filter enabled=True)
- `/tools`                     列全部内置工具(含未启用,带 ON / off 标记)

设计:**单数 = 显示当前状态(只读)**,**复数 = 列出全部**。
单数命令只读 ctx / 不查 DB(`/provider` / `/conversation`)或只展示 enabled
子集(`/tool`),复数走 repo list 全量。

**`/provider <name>` vs `/provider use <name>`**:前者只改本次 REPL 会话的
provider(本地 ctx,退出失效);后者持久化到 DB(下次 `chariot chat` 不传
`--provider` 时也走它)。CLI 顶层对应 `chariot chat --provider X`(本次)/
`chariot provider use X`(持久)。

输入交互
--------
prompt_toolkit:
- ↑ / ↓ 翻历史(持久化到 `~/.chariot/repl_history`,跨 session 沿用)
- Ctrl-R 反向搜索历史
- Tab 触发 slash 命令补全(候选见 `_SLASH_COMMANDS`)
- Ctrl-C / Ctrl-D 抛 KeyboardInterrupt / EOFError(语义跟 `input()` 一致)

状态持有
--------
会话状态(model / max_tokens / messages / conversation_id)全部在 `ChatContext`
实例里;持久化访问统一走 `services/*`。本类只负责"输入分派 + 打印"。
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
from chariot.services.conversation import ConversationService
from chariot.services.tool import ToolService

_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""ULID 26 字符;偏宽:Crockford base32 严格排除 I / L / O / U,但 chariot 整体不收紧。"""


@dataclass
class ChatRepl:
    """终端 REPL 会话。持有一个 `ChatContext`,循环读输入并分派命令。"""

    ctx: ChatContext

    # U+203A 单右尖引号,和普通 > 视觉上有区别,便于识别 REPL 提示符
    _PROMPT: ClassVar[str] = "› "

    _HISTORY_PATH: ClassVar[Path] = Path.home() / ".chariot" / "repl_history"
    """REPL 输入历史文件,跨 session 持久化。位置跟 `chariot.db` 同根。"""

    _SLASH_COMMANDS: ClassVar[list[str]] = [
        "/exit",
        "/quit",
        "/reset",
        "/help",
        "/agent",
        "/agents",
        "/conversation",
        "/convo",
        "/conversations",
        "/convos",
        "/tool",
        "/tools",
        "/skill",
        "/skills",
    ]
    """Tab 补全候选。新增 slash 命令时同步加这里(`_HELP` 文案是另一个真源)。"""

    _HELP: ClassVar[str] = (
        "slash 命令:\n"
        "  /exit, /quit           退出 REPL\n"
        "  /reset                 清空本地对话历史\n"
        "  /agent                 显示当前 agent_profile\n"
        "  /agent <name>          本次会话切换到指定 agent_profile\n"
        "  /agent clear           清空当前 agent_profile\n"
        "  /agents                列已注册 agent profiles\n"
        "  /conversation, /convo  显示当前会话\n"
        "  /conversation new      生成新 ULID 并切换\n"
        "  /convo new             `/conversation new` 别名\n"
        "  /conversation <ULID>   接续指定会话\n"
        "  /convo <ULID>          `/conversation <ULID>` 别名\n"
        "  /conversation off      切回 stateless\n"
        "  /convo off             `/conversation off` 别名\n"
        "  /conversations, /convos 列最近会话\n"
        "  /tool                  显示当前已启用工具\n"
        "  /tools                 列全部内置工具(含未启用)\n"
        "  /skill                 显示当前激活的 skill\n"
        "  /skill <name>          本次会话激活该 skill\n"
        "  /skill clear           清空当前 skill(显式空串覆盖 default_skill)\n"
        "  /skills                列 registry 可用 skill(builtin + DB union)\n"
        "  /help                  本说明"
    )

    async def _refresh_conversation_config(self) -> None:
        """每轮输入前重新查 DB,恢复 conversation 最新 agent 配置。
        若用户中途在 UI 改了,CLI REPL 能同步到最新值。DB 是 canonical 真源。
        provider 由 agent_profile 绑定自动推导,不再单独存储。
        """
        from chariot.database.session import DEFAULT_DB_PATH, init_db
        from chariot.services.conversation import ConversationService

        conv_id = self.ctx.conversation_id
        if conv_id is None:
            return
        assert isinstance(conv_id, str)
        sm = await init_db(DEFAULT_DB_PATH)
        async with sm():
            conv = await ConversationService(sm).get(conv_id)
        if conv is None:
            return
        # DB 是 canonical 真源:恢复 agent;provider 由 agent 推导
        if conv.agent_profile is not None:
            self.ctx.agent_profile = conv.agent_profile

    async def run(self) -> None:
        """主循环:读输入 → 分派 slash / 发请求 → 打印 meta 行。

        Ctrl+C / EOF / `/exit` / `/quit` 退出。
        """
        Renderer.out(
            f"chariot chat · agent={self.ctx.agent_profile or '(none)'}"
            + (f" · conversation={self.ctx.conversation_id}" if self.ctx.conversation_id else "")
            + " · /help 查看命令",
        )
        if self.ctx.agent_profile is None:
            Renderer.out("提示: 先用 `/agent <name>` 选择 agent_profile，再发送消息。")
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

            # C1: REPL 每轮输入前重新查 conversation 最新配置(防止 UI 中途改了)
            if self.ctx.conversation_id is not None:
                await self._refresh_conversation_config()

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
        请求发出后到第一个 event 到达前显示旋转 spinner(由 Renderer 统一管理,
        和 rich Live 不冲突)。
        """
        if self.ctx.agent_profile is None:
            Renderer.error_bubble("请先选择 agent_profile。用 `/agent <name>`。")
            return
        self.ctx.append_user(user_text)
        Renderer.start_spinner()
        try:
            result = await self.ctx.run_turn(Renderer.render_event)
        except ChatError as e:
            Renderer.stop_spinner()
            Renderer.stream_newline()
            self.ctx.pop_last()
            Renderer.error_bubble(f"{e.error_type}: {e.short_message()}")
            return
        finally:
            Renderer.stop_spinner()

        Renderer.stream_newline()
        self.ctx.append_assistant(result.text)
        Renderer.meta_line(
            provider=result.provider_name_snapshot,
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
        if cmd == "/agent":
            await self._slash_agent(arg)
            return False
        if cmd == "/agents":
            await self._slash_agents_list(arg)
            return False
        if cmd in ("/conversation", "/convo"):
            await self._slash_conversation(arg)
            return False
        if cmd in ("/conversations", "/convos"):
            await self._slash_conversations_list(arg)
            return False
        if cmd == "/tool":
            await self._slash_tool_show(arg)
            return False
        if cmd == "/tools":
            await self._slash_tools_list(arg)
            return False
        if cmd == "/skill":
            self._slash_skill(arg)
            return False
        if cmd == "/skills":
            self._slash_skills_list()
            return False

        Renderer.error_bubble(f"未知命令 {cmd!r};/help 查看可用命令")
        return False

    # ---------- /skill · /skills (B6 wave 2) ----------

    def _slash_skill(self, arg: str) -> None:
        """- `/skill` 显示当前 skill
        - `/skill <name>` 本次会话激活 <name>
        - `/skill clear` / `/skill ""` 显式清空(覆盖 agent_profile.default_skill)
        """
        if not arg:
            cur = self.ctx.skill
            if cur is None:
                Renderer.out("skill: (none; agent_profile.default_skill 起作用)")
            elif cur == "":
                Renderer.out("skill: (explicitly cleared)")
            else:
                Renderer.out(f"skill: {cur}")
            return
        if arg in ("clear", '""', "''"):
            self.ctx.set_skill("")
            Renderer.out("skill cleared (本次会话覆盖 default_skill)")
            return
        # 检查 skill 是否存在(给提示,不阻止)
        registry = self.ctx.agent.skill_registry
        if registry is not None and registry.get(arg) is None:
            Renderer.error_bubble(
                f"warning: skill {arg!r} 在 registry 里找不到;chat 仍会尝试,dangling 走 fallback",
            )
        self.ctx.set_skill(arg)
        Renderer.out(f"skill → {arg} (this session)")

    def _slash_skills_list(self) -> None:
        """`/skills` —— 列 registry 全部 skill。"""
        registry = self.ctx.agent.skill_registry
        if registry is None:
            Renderer.out("(skill registry not loaded)")
            return
        skills = registry.list_all()
        if not skills:
            Renderer.out("(no skills)")
            return
        for s in skills:
            badge = "ON" if s.enabled else "off"
            Renderer.out(f"  {badge}  {s.name}  [{s.source}]  {s.manifest.description}")

    # ---------- /agent ----------

    async def _slash_agent(self, arg: str) -> None:
        """- `/agent` 显示当前 agent_profile
        - `/agent <name>` 本次会话切换到 <name>(本地 + 同步到 conversation DB)
        - `/agent clear` / `/agent ""` 显式清空
        """
        if not arg:
            cur = self.ctx.agent_profile
            Renderer.out(f"agent_profile: {cur or '(none)'}")
            return

        if arg in ("clear", '""', "''"):
            self.ctx.agent_profile = None
            Renderer.out("agent_profile cleared")
        else:
            self.ctx.agent_profile = arg
            Renderer.out(f"agent_profile → {arg}")

        if self.ctx.conversation_id is not None:
            try:
                from chariot.services.conversation import ConversationService

                await ConversationService(self.ctx.agent).update_config(
                    self.ctx.conversation_id,
                    agent_profile=self.ctx.agent_profile,
                )
            except Exception:
                pass  # 同步失败不阻断

    async def _slash_agents_list(self, arg: str) -> None:
        """`/agents` — 列已注册的 agent profiles。"""
        if arg:
            Renderer.error_bubble(
                "/agents 不接受参数;切换 agent 用 `/agent <name>`",
            )
            return
        from chariot.services.agent import AgentService

        entries = await AgentService(self.ctx.agent).list_agents()
        if not entries:
            Renderer.out("(没有 agent profile — `chariot agent add` 加一个)")
            return
        rows = [
            (
                e.name,
                e.role or "-",
                e.provider_id or "-",
                e.toolset_id or "-",
                "← current" if e.name == self.ctx.agent_profile else "",
            )
            for e in entries
        ]
        Renderer.table(
            ["name", "role", "provider", "toolset_id", ""],
            rows,
            title="agent profiles",
        )

    # ---------- /conversation · /conversations ----------

    async def _slash_conversation(self, arg: str) -> None:
        """`/conversation` 显示当前会话;`/conversation new` 新建;
        `/conversation <ULID>` 切;`/conversation off` 切回 stateless。

        切会话会清空本地 self.messages —— DB 是该 ULID 历史的真源,本地累积
        的属上一会话,留着会让下一轮请求把上一会话的旧 turn 重新塞过去。
        """
        if not arg:
            self._show_current_convo()
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
            self.ctx.set_provider(None)
            self.ctx.agent_profile = None
            Renderer.out(f"conversation → {new_id} (new)")
            return

        if not _ULID_RE.match(arg):
            Renderer.error_bubble(
                f"非法 conversation 取值: {arg!r};应为 'new' / 'off' / 26 字符 ULID",
            )
            return

        # 切到已有会话:先查 DB 恢复 agent 配置;provider 由 agent 绑定自动推导
        try:
            from chariot.services.conversation import ConversationService

            conv = await ConversationService(self.ctx.agent).get(arg)
            if conv is not None and conv.agent_profile:
                self.ctx.agent_profile = conv.agent_profile
                from chariot.services.agent import AgentService

                agent = await AgentService(self.ctx.agent).get_agent(conv.agent_profile)
                if agent is not None and agent.provider_id:
                    self.ctx.set_provider(agent.provider_id)
        except Exception:
            pass

        self.ctx.conversation_id = arg
        self.ctx.reset()
        Renderer.out(f"conversation → {arg}")

    def _show_current_convo(self) -> None:
        """`/conversation` 无参数:打印当前会话快照(只读 ctx)。"""
        provider_s = self.ctx.provider_name or "(none)"
        if self.ctx.conversation_id is None:
            Renderer.out(f"conversation: off (stateless) · provider={provider_s}")
            return
        Renderer.out(
            f"conversation: {self.ctx.conversation_id} · provider={provider_s} · local msgs={len(self.ctx.messages)}",
        )

    async def _slash_conversations_list(self, arg: str) -> None:
        """`/conversations` — 列最近 20 条会话。"""
        if arg:
            Renderer.error_bubble(
                "/conversations 不接受参数;切会话用 `/conversation <ULID|new|off>`",
            )
            return
        conversations = await ConversationService(self.ctx.agent).list_conversations(limit=20)
        if not conversations:
            Renderer.out("(没有会话 — `/conversation new` 开一个)")
            return
        rows = [
            (
                c.id,
                _truncate(c.title or "(无标题)", 30),
                c.agent_profile or "-",
                str(c.message_count),
                "← current" if c.id == self.ctx.conversation_id else "",
            )
            for c in conversations
        ]
        Renderer.table(
            ["id", "title", "agent", "msgs", ""],
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
                "/tool 不接受参数;改启用 / options 用 `chariot tool enable|disable|config <name>` 或 GUI Tools 页",
            )
            return
        entries = await ToolService(self.ctx.agent).list_enabled()
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
        entries = await ToolService(self.ctx.agent).list_entries()
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
