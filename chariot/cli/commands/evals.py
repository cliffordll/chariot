"""`chariot eval` —— B2 evaluation loop。

Wave 1 范围:argparse skeleton。子命令:
- `chariot eval`              跑全套 golden task(wave 3 填真执行;现在 ERROR)
- `chariot eval list-tasks`   列 tests/golden/ 下已有 task
- `chariot eval list-runs`    列历史 run(`~/.chariot/eval/<timestamp>/`,wave 4 真填)

更多 flag(`--category`、`--task`、`--baseline`、`--diff`、`--no-save`)wave 3 / 4 加。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chariot.cli.render import Renderer
from chariot.eval.loader import GoldenTaskLoader, GoldenTaskLoadError

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
    no_save: Annotated[bool, typer.Option("--no-save", help="不落盘到 ~/.chariot/eval/")] = False,
) -> None:
    """无子命令时:跑全套(wave 3 真填)。"""
    if ctx.invoked_subcommand is not None:
        return
    asyncio.run(_run_all(category=category or None, task_id=task or None, no_save=no_save))


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


async def _run_all(*, category: str | None, task_id: str | None, no_save: bool) -> None:
    del no_save  # wave 4 用
    Renderer.out("(B2 wave 1: runner skeleton 还没接 AIAgent,跑批先空过 —— wave 3 填)")
    if category:
        Renderer.out(f"filter: category={category}")
    if task_id:
        Renderer.out(f"filter: task={task_id}")


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
