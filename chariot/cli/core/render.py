"""CLI 渲染工具:表格 / 状态行 / 错误气泡 / 流式 token。

`Renderer` 是名空间类(不实例化),所有方法 classmethod。

规则
----
- 成功信息:默认颜色(不染色),简洁一行 · stdout · 受 `QUIET` 影响
- 错误 / 失败:stderr 输出,红色 · **不受 `QUIET` 影响**(错误必须可见)
- 表格:rich.Table,边框用默认,标题灰度 · stdout · 受 `QUIET` 影响
- `--quiet` / `-q` 全局 flag 在 `cli/__main__.py` 的根 callback 里切 `Renderer.QUIET`
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from rich.console import Console
from rich.table import Table


class Renderer:
    """CLI 渲染工具集合(名空间,不实例化)。"""

    # Rich console:stdout 默认 / stderr 红色
    _stdout: ClassVar[Console] = Console()
    _stderr: ClassVar[Console] = Console(stderr=True, style="red")

    # 静默开关:True 时抑制 stdout 成功输出(stderr / 错误不变)
    QUIET: ClassVar[bool] = False

    # ---------- stdout(受 QUIET 影响)----------

    @classmethod
    def out(cls, msg: str) -> None:
        if cls.QUIET:
            return
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
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim")
        t.add_column()
        for k, v in pairs.items():
            t.add_row(k, cls._fmt_cell(v))
        cls._stdout.print(t)

    @classmethod
    def stream_token(cls, tok: str) -> None:
        """流式打印单个文本增量,立即 flush。

        用 `sys.stdout` 直写而非 rich,避免 rich 的行缓冲把逐 token 输出攒成整行;
        rich 控制台只在收尾打 meta 行时用。
        """
        if cls.QUIET:
            return
        sys.stdout.write(tok)
        sys.stdout.flush()

    @classmethod
    def stream_newline(cls) -> None:
        """流结束后换行,供 meta 行前使用。"""
        if cls.QUIET:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()

    @classmethod
    def meta_line(
        cls,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        path: str,
    ) -> None:
        """打 chat 收尾的 meta 行。

        形如 `[claude-haiku-4-5 · 8→21 tok · 412ms · messages]`。

        tok 数为 0 时显示 `?` 占位。`--quiet` 时完全抑制 meta 行。
        """
        if cls.QUIET:
            return
        in_s = str(input_tokens) if input_tokens > 0 else "?"
        out_s = str(output_tokens) if output_tokens > 0 else "?"
        line = f"[{model} · {in_s}→{out_s} tok · {latency_ms}ms · {path}]"
        cls._stdout.print(f"[dim]{line}[/dim]", highlight=False)

    # ---------- stderr(不受 QUIET 影响)----------

    @classmethod
    def err(cls, msg: str) -> None:
        """stderr 红字;错误输出必须可见,不受 QUIET 影响。"""
        cls._stderr.print(msg, highlight=False)

    @classmethod
    def die(cls, msg: str, *, code: int = 1) -> None:
        """打印错误到 stderr 并退出;不受 QUIET 影响。"""
        cls.err(msg)
        sys.exit(code)

    @classmethod
    def error_bubble(cls, msg: str) -> None:
        """REPL 里的内联错误,不退出;stderr + 前缀标记。不受 QUIET 影响。"""
        cls._stderr.print(f"[bold]x[/bold] {msg}", highlight=False)

    # ---------- 私有辅助 ----------

    @staticmethod
    def _fmt_cell(v: Any) -> str:
        if v is None:
            return "-"
        return str(v)
