"""CLI 渲染工具:表格 / 状态行 / 错误气泡 / 流式 token / ChatEvent dispatch。

`Renderer` 是名空间类(不实例化),所有方法 classmethod。

规则
----
- 成功信息:默认颜色(不染色),简洁一行 · stdout · 受 `QUIET` 影响
- 错误 / 失败:stderr 输出,红色 · **不受 `QUIET` 影响**(错误必须可见)
- 表格:rich.Table,边框默认,标题灰度 · stdout · 受 `QUIET` 影响
- `--quiet` / `-q` 全局 flag 在 `cli/__main__.py` 的根 callback 里切 `Renderer.QUIET`

ChatEvent dispatch(0.6.0)
--------------------------
`render_event(ev)` 消费 Claude 形态 `ChatEvent`,分派到各 sink:

- `content_block_delta` + `text_delta` → `stream_token(text)`(累进 Live)
- `content_block_start` + `tool_use` → 记 buffer(name + 空 input_json)
- `content_block_delta` + `input_json_delta` → buffer 拼 partial_json
- `content_block_stop`(对应 tool_use)→ 拼装完整 input,打 `tool_use_line`
- `tool_result` → `tool_result_line`(content 抽文本 + is_error)
- `message_stop` / `stream_done` / `error` → `_close_live`(把 markdown 定格)
- 其它(`message_start` / `message_delta` / `ping`)→ 静默

流式 markdown(0.5.1+ 沿用)
---------------------------
assistant 文本走 `rich.live.Live` + `rich.markdown.Markdown`:每个 text 增量
append 进 buffer 后用 Markdown(buffer) 重渲染 Live 区。tool_use_line /
tool_result_line / meta_line 触发时 `_close_live` 把 Live 收尾(把当前 markdown
定格写进 scrollback,后续 print 在它下面继续)。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table

if TYPE_CHECKING:
    from chariot.agent.chat_event import ChatEvent


class Renderer:
    """CLI 渲染工具集合(名空间,不实例化)。"""

    # Rich console:stdout 默认 / stderr 红色
    _stdout: ClassVar[Console] = Console()
    _stderr: ClassVar[Console] = Console(stderr=True, style="red")

    # 静默开关:True 时抑制 stdout 成功输出(stderr / 错误不变)
    QUIET: ClassVar[bool] = False

    # 流式 markdown 状态:assistant text 累积进 _live_buffer,Live 实时重渲染
    # Markdown(buffer)。tool_use_line / tool_result_line / meta_line 前 `_close_live`
    # 把当前 Live 区收尾(rich Live.stop() 把最后一帧定格写进 scrollback,后续
    # print 干净接续)。
    _live: ClassVar[Live | None] = None
    _live_buffer: ClassVar[str] = ""

    # tool_use 内容块缓冲:content_block_start(tool_use) 起,input_json_delta 拼,
    # content_block_stop 时一次性打 tool_use_line。key = block index。
    _tool_use_buffer: ClassVar[dict[int, dict[str, str]]] = {}

    # ---------- stdout(受 QUIET 影响)----------

    @classmethod
    def out(cls, msg: str) -> None:
        if cls.QUIET:
            return
        cls._close_live()
        cls._stdout.print(msg, highlight=False)

    @classmethod
    def table(
        cls,
        columns: list[str],
        rows: Iterable[Iterable[Any]],
        *,
        title: str | None = None,
    ) -> None:
        """打印 rich 表格;rows 传任意可迭代,元素会转 str。"""
        if cls.QUIET:
            return
        cls._close_live()
        t = Table(title=title, show_header=True, header_style="bold")
        for col in columns:
            t.add_column(col)
        for row in rows:
            t.add_row(*(cls._fmt_cell(v) for v in row))
        cls._stdout.print(t)

    @classmethod
    def kv(cls, pairs: Mapping[str, Any]) -> None:
        """打印 key/value 竖表;常用于 status / stats 汇总。"""
        if cls.QUIET:
            return
        cls._close_live()
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim")
        t.add_column()
        for k, v in pairs.items():
            t.add_row(k, cls._fmt_cell(v))
        cls._stdout.print(t)

    @classmethod
    def stream_token(cls, tok: str) -> None:
        """流式打印单个文本增量,送入 Live + Markdown 实时重渲染。

        首次调用懒启动 Live;后续调用只 update buffer。`_close_live` 把 Live 收
        尾(下次 stream_token 又会重启一个新 Live)。
        """
        if cls.QUIET:
            return
        cls._live_buffer += tok
        if cls._live is None:
            cls._live = Live(
                Markdown(cls._live_buffer, code_theme="ansi_dark"),
                console=cls._stdout,
                refresh_per_second=24,
                vertical_overflow="visible",
                transient=False,
            )
            cls._live.start()
        else:
            cls._live.update(Markdown(cls._live_buffer, code_theme="ansi_dark"))

    @classmethod
    def stream_newline(cls) -> None:
        """流结束后换行,供 meta 行前使用。Live 也在此收尾。"""
        if cls.QUIET:
            return
        cls._close_live()

    @classmethod
    def tool_use_line(cls, name: str, input_repr: str) -> None:
        """流式 tool_use 行,dim 灰 → 前缀。Live 收尾把 markdown 定格后再打。

        典型形态::

            →  read_file({"path": "x.txt"})
        """
        if cls.QUIET:
            return
        cls._close_live()
        cls._stdout.print(f"[dim]→ {name}({input_repr})[/dim]", highlight=False)

    @classmethod
    def render_event(cls, ev: ChatEvent) -> None:
        """`AIAgent.run` 吐的 ChatEvent → 屏幕输出。

        REPL / once / batch 共用同一份 dispatch 逻辑(都把这个静态方法当 on_event
        回调传给 `ChatContext.run_turn`)。dispatch 规则见模块 docstring。
        """
        match ev.kind:
            case "content_block_start":
                cls._on_content_block_start(ev)
            case "content_block_delta":
                cls._on_content_block_delta(ev)
            case "content_block_stop":
                cls._on_content_block_stop(ev)
            case "tool_result":
                cls._on_tool_result(ev)
            case "message_stop" | "stream_done" | "error":
                cls._close_live()
            case "message_start" | "message_delta" | "ping":
                # 内核 / token 元事件,渲染层不展示;tokens 的累积由 ChatContext 做
                pass

    @classmethod
    def _on_content_block_start(cls, ev: ChatEvent) -> None:
        """tool_use 块起头时记 buffer;text 块不需要(stream_token 自己懒启 Live)。"""
        cb = ev.content_block or {}
        if cb.get("type") != "tool_use" or ev.index is None:
            return
        cls._tool_use_buffer[ev.index] = {
            "name": str(cb.get("name", "")),
            "input_json": "",
        }

    @classmethod
    def _on_content_block_delta(cls, ev: ChatEvent) -> None:
        """text_delta 立即流式打;input_json_delta 拼到对应 tool_use buffer。"""
        delta = ev.delta or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            cls.stream_token(str(delta.get("text", "")))
            return
        if delta_type == "input_json_delta" and ev.index is not None:
            buf = cls._tool_use_buffer.get(ev.index)
            if buf is not None:
                buf["input_json"] += str(delta.get("partial_json", ""))

    @classmethod
    def _on_content_block_stop(cls, ev: ChatEvent) -> None:
        """tool_use 块结束时把累积 input_json 当 input_repr 打 tool_use_line。"""
        if ev.index is None or ev.index not in cls._tool_use_buffer:
            return
        buf = cls._tool_use_buffer.pop(ev.index)
        cls.tool_use_line(buf["name"], buf["input_json"] or "{}")

    @classmethod
    def _on_tool_result(cls, ev: ChatEvent) -> None:
        """tool_result event → tool_result_line。content 可为 str / list[block]。"""
        cls.tool_result_line(cls._extract_tool_result_text(ev.content), is_error=ev.is_error)

    @staticmethod
    def _extract_tool_result_text(content: str | list[dict[str, Any]] | None) -> str:
        """tool_result 的 content 可能是字符串或 Claude tool_result content blocks 数组。

        blocks 数组下抽出所有 `type=text` 的 text 字段拼接;非 text 块(image
        等)忽略,反正 CLI 终端只能展文本。
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for block in content:
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)

    @classmethod
    def tool_result_line(cls, text: str, *, is_error: bool = False) -> None:
        """流式 tool_result 行。成功 dim 灰 ← 前缀;错误红色。

        长 text 截到首行末尾(避免一条 tool_result 把屏幕灌满);完整内容靠 `chariot
        conversation show` 看。
        """
        if cls.QUIET:
            return
        cls._close_live()
        # 截首行 + 限长(避免 tool_result 灌屏)
        first_line = text.splitlines()[0] if text else "(空)"
        truncated = first_line if len(first_line) <= 120 else first_line[:119] + "…"
        if is_error:
            cls._stdout.print(f"[red]← error: {truncated}[/red]", highlight=False)
        else:
            cls._stdout.print(f"[dim]← {truncated}[/dim]", highlight=False)

    @classmethod
    def meta_line(
        cls,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> None:
        """打 chat 收尾的 meta 行。

        形如 `[claude-haiku-4-5 · 8→21 tok · 412ms]`。

        tok 数为 0 时显示 `?` 占位。`--quiet` 时完全抑制 meta 行。
        """
        if cls.QUIET:
            return
        cls._close_live()
        in_s = str(input_tokens) if input_tokens > 0 else "?"
        out_s = str(output_tokens) if output_tokens > 0 else "?"
        line = f"[{model} · {in_s}→{out_s} tok · {latency_ms}ms]"
        cls._stdout.print(f"[dim]{line}[/dim]", highlight=False)

    # ---------- stderr(不受 QUIET 影响)----------

    @classmethod
    def err(cls, msg: str) -> None:
        """stderr 红字;错误输出必须可见,不受 QUIET 影响。"""
        cls._close_live()
        cls._stderr.print(msg, highlight=False)

    @classmethod
    def die(cls, msg: str, *, code: int = 1) -> None:
        """打印错误到 stderr 并退出;不受 QUIET 影响。"""
        cls.err(msg)
        sys.exit(code)

    @classmethod
    def error_bubble(cls, msg: str) -> None:
        """REPL 里的内联错误,不退出;stderr + 前缀标记。不受 QUIET 影响。"""
        cls._close_live()
        cls._stderr.print(f"[bold]x[/bold] {msg}", highlight=False)

    # ---------- 私有辅助 ----------

    @classmethod
    def _close_live(cls) -> None:
        """收尾当前 Live(若开),把 markdown 定格进 scrollback,清空 buffer。

        rich.Live.stop() 行为:做最后一次 render 把内容固定下来,后续 print 在它
        下面继续。再调 stream_token 会重启新 Live(下一个 assistant 段)。
        """
        if cls._live is not None:
            try:
                cls._live.stop()
            finally:
                cls._live = None
                cls._live_buffer = ""

    @classmethod
    def _reset_for_tests(cls) -> None:
        """测试 fixture 用:清掉 Live 状态 + tool_use buffer,避免跨 case 串味。

        生产路径不会调(`_close_live` 已经是状态收尾的真源)。
        """
        cls._close_live()
        cls._tool_use_buffer.clear()

    @staticmethod
    def _fmt_cell(v: Any) -> str:
        if v is None:
            return "-"
        return str(v)
