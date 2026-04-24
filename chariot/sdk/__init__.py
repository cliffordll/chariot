"""chariot SDK:发现 / 启动 server + 封装管理面 + 数据面 chat。

入口
----
- `ProxyClient.discover_session()`:连到本机 server(不在就自动 spawn)
- `ProxyClient.chat_once(text, ...)`:发一条消息拿 `ChatResult`(非流)
- `ProxyClient.stream_chat(fmt, body)` + `ChatStream(fmt).text_deltas(resp)`:流式
- `ServerDiscovery.find_or_spawn(...)`:低层级发现 / 启动 server 的编排
"""

from __future__ import annotations

from chariot.sdk.chat import ChatResult
from chariot.sdk.client import ProxyClient
from chariot.sdk.discover import ServerDiscovery
from chariot.sdk.streams import ChatStream, SseParser

__all__ = [
    "ChatResult",
    "ChatStream",
    "ProxyClient",
    "ServerDiscovery",
    "SseParser",
]
