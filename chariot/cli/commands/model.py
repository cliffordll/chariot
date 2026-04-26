"""`chariot model list / use` —— 列 model + 切 active。

二级子命令组(typer.Typer 嵌套):
- `chariot model list`:GET /admin/models 列出 available + active 标记 + 已注册 type
- `chariot model use <name>`:POST /admin/models 切到指定 name

server 未运行 → 错误退出(不自动 spawn,因为切换 model 是对运行中 server 的操作)。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import httpx
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient

model_app = typer.Typer(
    name="model",
    help="管理 active model(配置在 ~/.chariot/config.toml)",
    no_args_is_help=True,
)


# ---------- list ----------


@model_app.command("list", help="列出可用 model + 当前 active")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            data = await client.list_models()
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    if not data.available:
        Renderer.out("(配置里没有 model — 当前走 MockModel fallback)")
    else:
        rows = [("✓" if name == data.active else " ", name) for name in data.available]
        Renderer.table(["", "model"], rows, title="可用 model")

    Renderer.out(f"已注册 type:{', '.join(data.types)}")


# ---------- use ----------


@model_app.command("use", help="切到指定 model name")
def use_cmd(
    name: Annotated[str, typer.Argument(help="model name(必须在 config.toml 里定义)")],
) -> None:
    asyncio.run(_use(name))


async def _use(name: str) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                result = await client.use_model(name)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"切换失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    Renderer.out(f"active → {result.active} (model={result.model})")


def register(app: typer.Typer) -> None:
    app.add_typer(model_app)
