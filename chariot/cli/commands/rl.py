"""`chariot rl <subcmd>` —— B7 wave 1 起。

子命令:
- `chariot rl export <conversation_id> [--out path] [--raw]` —— 把一条对话
  的完整 trajectory 导出为 JSONL(默认 `~/.chariot/rl/trajectories/<conv-id>.jsonl`)
  - `--raw`:跳过 SecretScrubber(本地训练自用;打 banner 警告)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.rl import NullScrubber, SecretScrubber, TrajectoryExporter

rl_app = typer.Typer(
    name="rl",
    help="RL 数据 pipeline(trajectory export + reward annotate + dataset pack)",
    no_args_is_help=True,
)


@rl_app.command("export", help="导出一条 conversation 的完整 trajectory 到 JSONL")
def export_cmd(
    conversation_id: Annotated[str, typer.Argument(help="conversation id")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="输出 JSONL 路径(默认 ~/.chariot/rl/trajectories/<conv-id>.jsonl)"),
    ] = None,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="不跑 SecretScrubber(本地训练自用;⚠️ 含 secret 风险)"),
    ] = False,
) -> None:
    asyncio.run(_export(conversation_id, out, raw))


async def _export(conversation_id: str, out: Path | None, raw: bool) -> None:
    out_path = out or _default_out_path(conversation_id)
    if raw:
        Renderer.out("⚠️  --raw 模式:不扫除 secret,trajectory 可能含 API key / token")
    scrubber = NullScrubber() if raw else SecretScrubber()
    async with installed_runtime() as agent:
        exporter = TrajectoryExporter(
            sessionmaker=agent.session_maker,
            scrubber=scrubber,
            audit_hooks=agent.audit_hooks,
        )
        row_count = await exporter.export_to_jsonl(conversation_id, out_path)
    Renderer.out(f"+ {row_count} rows → {out_path}")


def _default_out_path(conversation_id: str) -> Path:
    base = Path.home() / ".chariot" / "rl" / "trajectories"
    return base / f"{conversation_id}.jsonl"


def register(app: typer.Typer) -> None:
    app.add_typer(rl_app)
