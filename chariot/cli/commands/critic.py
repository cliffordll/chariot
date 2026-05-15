"""`chariot critic <subcmd>` —— Critic 副 LLM(B4 wave 1)。

子命令(wave 1 只有 1 个 + 1 个状态查询):
- `chariot critic try "<task>" --produced "<output>" [--context "<ctx>"]`:
  手动跑一次 critique,打印 verdict + reason(调试 critic prompt / 阈值用)
- `chariot critic show`:显示当前装载的 critic aux entry(没装载 = 提示去
  `chariot auxiliary add --name critic ...`)

复用 B3 wave 2 的 `auxiliary_clients` 表;`name='critic'` 行就是 critic LLM。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer

critic_app = typer.Typer(
    name="critic",
    help="管理 / 调用 critic 副 LLM(B4)",
    no_args_is_help=True,
)


@critic_app.command("show", help="显示当前装载的 critic auxiliary client")
def show_cmd() -> None:
    asyncio.run(_show())


async def _show() -> None:
    async with installed_runtime() as agent:
        critic = agent.critic_agent
    if critic is None:
        Renderer.out("(no critic loaded — 用 `chariot auxiliary add --name critic ...` 装一个)")
        return
    entry = critic.aux.entry
    Renderer.out(f"name:           {entry.name}")
    Renderer.out(f"provider_id:    {entry.provider_id}")
    Renderer.out(f"model:          {entry.model or '(继承 provider)'}")
    Renderer.out(f"params:         {json.dumps(entry.params, ensure_ascii=False)}")


@critic_app.command("try", help="手动跑一次 critique(调试 prompt 用)")
def try_cmd(
    task: Annotated[str, typer.Argument(help="任务目标(自然语言)")],
    produced: Annotated[str, typer.Option("--produced", "-p", help="主 agent 的产出")] = "",
    context: Annotated[str, typer.Option("--context", "-c", help="额外上下文(可选)")] = "",
) -> None:
    if not produced:
        raise typer.BadParameter("必须传 --produced(主 agent 产出);没产出无法裁决")
    asyncio.run(_try(task, produced, context or None))


async def _try(task: str, produced: str, context: str | None) -> None:
    async with installed_runtime() as agent:
        critic = agent.critic_agent
        if critic is None:
            Renderer.die("no critic loaded — 用 `chariot auxiliary add --name critic ...` 装一个")
            return
        verdict = await critic.critique(task_goal=task, produced=produced, extra_context=context)
    Renderer.out(f"verdict: {verdict.verdict}")
    Renderer.out(f"reason:  {verdict.reason}")
    if verdict.raw and verdict.raw != verdict.reason:
        Renderer.out("--- raw ---")
        Renderer.out(verdict.raw)


def register(app: typer.Typer) -> None:
    app.add_typer(critic_app)
