"""`chariot eval` —— B2 evaluation loop。

子命令:
- `chariot eval`              跑全套 golden task(可加 --category / --task 过滤)
- `chariot eval list-tasks`   列 tests/golden/ 下已有 task
- `chariot eval list-runs`    列历史 run(`~/.chariot/eval/<timestamp>/`)
- `chariot eval show <run>`   看单个历史 run 详情(report.txt 输出)

Flags:
- `--agent <name>`             指定 agent_profile(走 AIAgent._resolve_binding 三件套)
- `--category <c>` / `--task <id>` 过滤
- `--dir <path>`               golden task 目录(默认 tests/golden/)
- `--baseline <run-id>`        以指定历史 run 为基线,跑完后输出 verdict diff
- `--no-save`                  不落盘到 ~/.chariot/eval/<timestamp>/

每次跑批默认落:`meta.json` / `tasks.json` / `records.json` / `summary.json` /
`report.txt`。落盘后 CLI 末尾打印 `saved run: <run_id>`,方便后续 `--baseline`
引用。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.eval.diff import EvalDiff
from chariot.eval.loader import GoldenTaskLoader, GoldenTaskLoadError
from chariot.eval.report import EvalReport
from chariot.eval.runner import EvalRunner
from chariot.eval.store import EvalRunStore
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
    baseline: Annotated[str, typer.Option("--baseline", help="基线 run_id;跑完输出 diff")] = "",
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
            baseline_run_id=baseline or None,
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


@eval_app.command("show", help="看单个历史 eval run 的 report.txt")
def show_run_cmd(
    run_id: Annotated[str, typer.Argument(help="run_id(目录名,看 list-runs)")],
    runs_dir: Annotated[Path, typer.Option("--dir", help="eval runs 根目录")] = _DEFAULT_RUNS_DIR,
) -> None:
    asyncio.run(_show_run(run_id, runs_dir))


async def _run_all(
    *,
    category: str | None,
    task_id: str | None,
    agent_profile: str | None,
    golden_dir: Path,
    baseline_run_id: str | None,
    no_save: bool,
) -> None:
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
            del _task
            return agent

        runner = EvalRunner(agent_factory=_factory, agent_profile=agent_profile)
        records = await runner.run_all(tasks)

    for line in EvalReport.render_lines(records):
        Renderer.out(line)

    store = EvalRunStore()
    saved_run_id: str | None = None
    if not no_save:
        saved_run_id = store.save(
            tasks=tasks,
            records=records,
            meta={"agent_profile": agent_profile, "golden_dir": str(golden_dir)},
        )
        Renderer.out("")
        Renderer.out(f"saved run: {saved_run_id}")

    if baseline_run_id:
        _print_diff(
            store,
            baseline_run_id=baseline_run_id,
            current_run_id=saved_run_id,
            current_records=records,
            current_tasks=tasks,
        )


def _print_diff(
    store: EvalRunStore,
    *,
    baseline_run_id: str,
    current_run_id: str | None,
    current_records,
    current_tasks,
) -> None:
    """跑完跟 baseline diff;current 可来自刚 save 的 run(优先 load 回来保证字段顺序对齐)
    或落地 no_save 模式下临时合成的 in-memory snapshot。
    """
    from chariot.eval.store import EvalRunSnapshot

    baseline = store.load(baseline_run_id)
    if baseline is None:
        Renderer.out("")
        Renderer.out(f"(baseline run_id {baseline_run_id!r} 不存在;跳过 diff)")
        return
    if current_run_id is not None:
        current = store.load(current_run_id)
    else:
        # --no-save:用 in-memory 合成 snapshot,只填 records / tasks(diff 不需要 summary / meta)
        current = EvalRunSnapshot(
            run_id="(unsaved)",
            run_dir=Path(),
            meta={},
            tasks=list(current_tasks),
            records=list(current_records),
            summary={},
        )
    if current is None:
        return
    entries = EvalDiff.compute(baseline=baseline, current=current)
    Renderer.out("")
    Renderer.out(f"diff vs baseline {baseline_run_id}:")
    for line in EvalDiff.render_lines(entries):
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
    store = EvalRunStore(root=runs_dir)
    run_ids = store.list_runs()
    if not run_ids:
        Renderer.out(f"(还没有 eval run 落过盘:{runs_dir})")
        return
    rows: list[tuple[str, str, str, str]] = []
    for run_id in run_ids:
        snap = store.load(run_id)
        if snap is None:
            rows.append((run_id, "-", "-", "-"))
            continue
        summary = snap.summary
        rate = f"{summary.get('pass_rate', 0):.0%}" if summary else "-"
        passed = str(summary.get("passed", "-"))
        total = str(summary.get("total", "-"))
        rows.append((run_id, f"{passed}/{total}", rate, snap.meta.get("agent_profile") or "-"))
    Renderer.table(["run_id", "passed", "rate", "agent_profile"], rows, title="eval runs")


async def _show_run(run_id: str, runs_dir: Path) -> None:
    store = EvalRunStore(root=runs_dir)
    snap = store.load(run_id)
    if snap is None:
        Renderer.die(f"run_id {run_id!r} 不存在(看 {runs_dir})")
        return
    report_path = snap.run_dir / "report.txt"
    if report_path.is_file():
        Renderer.out(report_path.read_text(encoding="utf-8"))
        return
    # 兜底:report.txt 缺失就现场渲染
    for line in EvalReport.render_lines(snap.records):
        Renderer.out(line)


def register(app: typer.Typer) -> None:
    app.add_typer(eval_app)
