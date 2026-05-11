"""`chariot guardrail <subcmd>` —— Guardrails 查看 + dry-run(B5 wave 1)。

子命令:
- `chariot guardrail list`:列 13 条内置规则 + 当日配额剩余
- `chariot guardrail try <tool_name> --args '<JSON>'`:dry-run 一次评估
  (不消耗配额),打印 verdict + rule + reason
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.guardrails import Verdict

guardrail_app = typer.Typer(
    name="guardrail",
    help="工具调用拦截规则(B5)",
    no_args_is_help=True,
)


@guardrail_app.command("list", help="列内置规则 + 当日配额剩余")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent:
        engine = agent.guardrail_engine
    if engine is None:
        Renderer.out("(no guardrail engine loaded)")
        return
    rows: list[tuple[str, str, str, str]] = []
    for rule in engine.rules:
        rem = engine.quota.peek_remaining(rule.rule_id, quota=rule.daily_quota)
        quota_col = "∞" if rule.daily_quota is None else f"{rem}/{rule.daily_quota}"
        rows.append((rule.rule_id, rule.verdict.value, quota_col, rule.description))
    Renderer.table(["rule_id", "verdict", "quota", "description"], rows, title="guardrail rules")


@guardrail_app.command("try", help="dry-run 一次评估(不消耗配额)")
def try_cmd(
    tool_name: Annotated[str, typer.Argument(help="工具名(如 shell_exec / write_file)")],
    args: Annotated[
        str,
        typer.Option("--args", "-a", help='工具 args JSON(如 \'{"command":"rm -rf /"}\')'),
    ] = "{}",
) -> None:
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"--args 不是合法 JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--args 必须是 JSON object")
    asyncio.run(_try(tool_name, parsed))


async def _try(tool_name: str, args: dict[str, object]) -> None:
    async with installed_runtime() as agent:
        engine = agent.guardrail_engine
    if engine is None:
        Renderer.die("no guardrail engine loaded")
        return
    verdict = engine.preview(tool_name=tool_name, args=args)
    Renderer.out(f"verdict:        {verdict.verdict.value}")
    Renderer.out(f"rule_id:        {verdict.rule_id}")
    Renderer.out(f"reason:         {verdict.reason}")
    if verdict.matched_pattern is not None:
        Renderer.out(f"matched:        {verdict.matched_pattern!r}")
    if verdict.quota_remaining is not None:
        Renderer.out(f"quota_remaining: {verdict.quota_remaining}")
    if verdict.quota_exhausted:
        Renderer.out("(quota exhausted → auto-downgrade DENY)")
    # 退出码:DENY → 1,REQUIRE_APPROVAL → 0(给 CI 用)
    if verdict.verdict == Verdict.DENY:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    app.add_typer(guardrail_app)
