"""三协议标识 + 上游路径映射。

- `Protocol`:API 协议(/v1/messages / /v1/chat/completions / /v1/responses)
- `UPSTREAM_PATH`:protocol → 上游 URL 路径

`upstream.protocol` 直接用 Protocol 枚举值(`messages` / `completions` / `responses`),
与 CLI `--protocol` / HTTP path 四层一致。

`upstream.base_url` 在 v0 是必填字段,不再依赖"按 protocol 取官方默认"的兜底表。
"""

from __future__ import annotations

from enum import StrEnum


class Protocol(StrEnum):
    MESSAGES = "messages"
    CHAT_COMPLETIONS = "completions"
    RESPONSES = "responses"


UPSTREAM_PATH: dict[Protocol, str] = {
    Protocol.MESSAGES: "/v1/messages",
    Protocol.CHAT_COMPLETIONS: "/v1/chat/completions",
    Protocol.RESPONSES: "/v1/responses",
}
