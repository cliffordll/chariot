"""`chariot eval` —— B2 evaluation loop。

子命令:
- `chariot eval`              跑全套 golden task(可加 --category / --task 过滤)
- `chariot eval list-tasks`   列 tests/golden/ 下已有 task
- `chariot eval list-runs`    列历史 run(`~/.chariot/eval/<timestamp>/`,wave 4 真填)

`chariot eval` 跑批走真 AIAgent(`installed_runtime`):每个 task 用 unique
conversation_id;agent_profile 取自 `--agent`(若提供),否则走默认 provider
+ active prompt bundle + 全量 enabled tools。

更多 flag(`--baseline`、`--diff`、`--no-save`)wave 4 加。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.eval.loader import GoldenTaskLoader, GoldenTaskLoadError
from chariot.eval.report import EvalReport
from chariot.eval.runner import EvalRunner
from chariot.models.eval import GoldenTask

eval_app = typer.Typer(
    name="eval",
    help="B2 evaluation loop(golden task + verifier + diff)",
    no_args_is_help=False,
)


_DEFAULT_GOLDEN_DIR = Path("tests/golden")
_DEFAULT_RUNS_DIR = Path.home() / ".chariot" / "eval"


@eval_app.callback(invoke_without_command=True)
def eval_root(
    ctx: typer.Context,
    category: Annotated[str, typer.Option("--category", help="只跑该 category")] = "",
    task: Annotated[str, typer.Option("--task", help="只跑该 task_id")] = "",
    agent_profile: Annotated[str, typer.Option("--agent", help="跑批用指定 agent_profile")] = "",
    golden_dir: Annotated[Path, typer.Option("--dir", help="golden task 目录")] = _DEFAULT_GOLDEN_DIR,
    no_save: Annotated[bool, typer.Option("--no-save", help="不落盘到 ~/.chariot/eval/")] = False,
) -> None:
    """无子命令时:跑全套。"""
    if ctx.invoked_subcommand is not None:
        return
    asyncio.run(
        _run_all(
            category=category or None,
            task_id=task or None,
            agent_profile=agent_profile or None,
            golden_dir=golden_dir,
            no_save=no_save,
        )
    )


@eval_app.command("list-tasks", help="列 tests/golden/ 下已有 golden task")
def list_tasks_cmd(
    golden_dir: Annotated[Path, typer.Option("--dir", help="golden task 目录")] = _DEFAULT_GOLDEN_DIR,
) -> None:
    asyncio.run(_list_tasks(golden_dir))


@eval_app.command("list-runs", help="列历史 eval run(~/.chariot/eval/<timestamp>/)")
def list_runs_cmd(
    runs_dir: Annotated[Path, typer.Option("--dir", help="eval runs 根目录")] = _DEFAULT_RUNS_DIR,
) -> None:
    asyncio.run(_list_runs(runs_dir))


async def _run_all(
    *,
    category: str | None,
    task_id: str | None,
    agent_profile: str | None,
    golden_dir: Path,
    no_save: bool,
) -> None:
    del no_save  # wave 4 用
    try:
        tasks = GoldenTaskLoader.load_dir(golden_dir)
    except GoldenTaskLoadError as exc:
        Renderer.die(str(exc))
        return
    tasks = _filter_tasks(tasks, category=category, task_id=task_id)
    if not tasks:
        Renderer.out("(过滤后没有可跑的 task)")
        return

    async with installed_runtime() as agent:

        def _factory(_task: GoldenTask):
            # CLI 跑批所有 task 共享同一个 AIAgent 实例 —— 装载成本只付一次,符合 B2 串行语义
            del _task
            return agent

        runner = EvalRunner(agent_factory=_factory, agent_profile=agent_profile)
        records = await runner.run_all(tasks)

    for line in EvalReport.render_lines(records):
        Renderer.out(line)


def _filter_tasks(tasks: list[GoldenTask], *, category: str | None, task_id: str | None) -> list[GoldenTask]:
    if task_id:
        return [t for t in tasks if t.task_id == task_id]
    if category:
        return [t for t in tasks if t.category == category]
    return tasks


async def _list_tasks(golden_dir: Path) -> None:
    if not golden_dir.is_dir():
        Renderer.out(f"(golden 目录不存在:{golden_dir})")
        return
    try:
        tasks = GoldenTaskLoader.load_dir(golden_dir)
    except GoldenTaskLoadError as exc:
        Renderer.die(str(exc))
        return
    if not tasks:
        Renderer.out(f"(没有 golden task,目录 {golden_dir} 为空)")
        return
    rows = [(t.task_id, t.category, t.verifier_type, t.description or "-") for t in tasks]
    Renderer.table(["task_id", "category", "verifier", "description"], rows, title="golden tasks")


async def _list_runs(runs_dir: Path) -> None:
    if not runs_dir.is_dir():
        Renderer.out(f"(还没有 eval run 落过盘:{runs_dir})")
        return
    runs = sorted([p for p in runs_dir.iterdir() if p.is_dir()], reverse=True)
    if not runs:
        Renderer.out(f"(目录存在但无 run:{runs_dir})")
        return
    rows = [(p.name, str(p)) for p in runs]
    Renderer.table(["timestamp", "path"], rows, title="eval runs")


def register(app: typer.Typer) -> None:
    app.add_typer(eval_app)
