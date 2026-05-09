"""`chariot` CLI 入口。

子命令(0.6.0 库化后,撤 daemon `start` / `stop`;0.6.0 起
`model` rename → `provider`,`conversation` rename → `convo`):

- `chariot status`     —— DB 路径 / providers / tools / version
- `chariot logs [-n N]`
- `chariot stats [period]`
- `chariot chat [text]` —— 一次性 / REPL,直接构造 AIAgent
- `chariot provider ...` / `tool ...` / `convo ...`
- `chariot memory ...` / `eval ...` / `skill ...` / `checkpoint ...`
"""

from __future__ import annotations

from typing import Annotated

import typer

from chariot.cli.commands import (
    chat as chat_mod,
)
from chariot.cli.commands import (
    checkpoint as checkpoint_mod,
)
from chariot.cli.commands import (
    convo as convo_mod,
)
from chariot.cli.commands import (
    evals as eval_mod,
)
from chariot.cli.commands import (
    logs as logs_mod,
)
from chariot.cli.commands import (
    memory as memory_mod,
)
from chariot.cli.commands import (
    provider as provider_mod,
)
from chariot.cli.commands import (
    skill as skill_mod,
)
from chariot.cli.commands import (
    stats as stats_mod,
)
from chariot.cli.commands import (
    status as status_mod,
)
from chariot.cli.commands import (
    tool as tool_mod,
)

# 所有子 Typer 共享的 context 配置:让 `-h` 也能触发 help(默认只认 `--help`)
HELP_CONTEXT: dict[str, list[str]] = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name="chariot",
    help="chariot — 本地智能体 CLI",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings=HELP_CONTEXT,
)


@app.callback()
def _root(  # pyright: ignore[reportUnusedFunction] — typer @app.callback() 装饰器注册
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="静默模式:抑制成功输出(错误仍打 stderr)"),
    ] = False,
) -> None:
    """根 callback:处理全局 flag。子命令执行前会先跑这里。"""
    from chariot.cli.render import Renderer

    Renderer.QUIET = quiet


for mod in (
    status_mod,
    logs_mod,
    stats_mod,
    chat_mod,
    provider_mod,
    tool_mod,
    convo_mod,
    memory_mod,
    eval_mod,
    skill_mod,
    checkpoint_mod,
):
    mod.register(app)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
