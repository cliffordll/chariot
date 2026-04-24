"""`chariot status` — server 状态 + upstream 计数。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient


def status_cmd() -> None:
    """显示 server 状态(running / not running)+ upstream 计数。"""
    asyncio.run(_run())


async def _run() -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            st = await client.status()
            upstreams = await client.list_upstreams()
            Renderer.kv(
                {
                    "server": client.base_url,
                    "version": st.version,
                    "uptime_ms": st.uptime_ms,
                    "upstreams": f"{st.upstreams_count} ({len(upstreams)} via list)",
                }
            )
    except RuntimeError:
        Renderer.out("not running")


def register(app: typer.Typer) -> None:
    app.command("status", help="显示 server 状态")(status_cmd)
