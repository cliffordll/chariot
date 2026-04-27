"""`chariot status` — server 状态。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient


def status_cmd() -> None:
    """显示 server 状态(running / not running)。"""
    asyncio.run(_run())


async def _run() -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            st = await client.status()
            Renderer.kv(
                {
                    "server": client.base_url,
                    "version": st.version,
                    "uptime_ms": st.uptime_ms,
                    "entries": st.entries_count,
                    "url": st.url,
                }
            )
    except RuntimeError:
        Renderer.out("not running")


def register(app: typer.Typer) -> None:
    app.command("status", help="显示 server 状态")(status_cmd)
