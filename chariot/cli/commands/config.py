"""`chariot config init / show` —— 模板复制 + 当前路径排错。

二级子命令:
- `chariot config init [--force]`:把内置 `chariot/config.example.toml` 复制到
  `~/.chariot/config.toml`。默认拒绝覆盖已存在的文件;`--force` 强行覆盖。
- `chariot config show`:打默认路径 + env `CHARIOT_CONFIG`(若设)+ 当前内容。
  排错时用户能一眼看清"chariot 实际读哪个文件"。

模板源文件以 importlib.resources 读取(打包后 wheel 里可拿到)。
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

from chariot.cli.core.render import Renderer
from chariot.server.config import ConfigLoader

config_app = typer.Typer(
    name="config",
    help="管理 ~/.chariot/config.toml(模板初始化 / 当前路径)",
    no_args_is_help=True,
)


# ---------- init ----------


@config_app.command("init", help="把内置模板复制到 ~/.chariot/config.toml")
def init_cmd(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="覆盖已存在的 config.toml(默认拒绝)"),
    ] = False,
) -> None:
    target = ConfigLoader.DEFAULT_PATH
    if target.exists() and not force:
        Renderer.die(
            f"{target} 已存在;加 --force 覆盖,或直接编辑现有文件。",
        )
        return

    template = files("chariot").joinpath("config.example.toml").read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template, encoding="utf-8")

    Renderer.out(f"模板写入 {target}")
    Renderer.out(
        "编辑该文件:在 [models.options] 填 api_key,或保留 api_key_env 并 export 该 env;"
        "改完重启 server 即可。"
    )


# ---------- show ----------


@config_app.command("show", help="打当前 config 路径 + 内容(排错用)")
def show_cmd() -> None:
    default_path = ConfigLoader.DEFAULT_PATH
    env_value = os.environ.get(ConfigLoader.ENV_OVERRIDE)
    effective = Path(env_value) if env_value else default_path

    Renderer.out(f"默认路径: {default_path}")
    if env_value:
        Renderer.out(f"env {ConfigLoader.ENV_OVERRIDE}: {env_value}(覆盖默认)")
    Renderer.out(f"实际生效: {effective}")

    if not effective.exists():
        Renderer.out("(文件不存在 — server 启动会走 MockModel fallback)")
        return

    Renderer.out("---")
    Renderer.out(effective.read_text(encoding="utf-8"))


def register(app: typer.Typer) -> None:
    app.add_typer(config_app)
