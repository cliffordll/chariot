"""/v1/* 数据面路由。

0.2.0 起 chariot 只对外暴露 `POST /v1/messages`(Anthropic Messages 协议);
OpenAI 客户端通过外部转换器(LiteLLM / claude-code-router 等)接入,本服务
不做协议翻译。详见 `docs/DESIGN.md` §3。

0.4.0 加可选 `X-Chariot-Conversation` HTTP header(模式 A/B/C 详见
DESIGN §3.1)。header 缺失等价 0.3.1 stateless 行为(完全向后兼容)。

route 是哑管道:读 body + 校验 header + 调 `Agent.current().handle(...)`,
具体的 conversation 持久化 / 工具循环逻辑由 Agent 层负责。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from chariot.server.agent import Agent
from chariot.server.database.session import SessionDep
from chariot.server.service.exceptions import ServiceError

router = APIRouter()

# ULID 严格 26 字符 base32 大写(DESIGN §3.1 决策:不接受任意人类可读字符串)
_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")


@router.post("/v1/messages")
async def messages(
    request: Request,
    session: SessionDep,
    x_chariot_conversation: str | None = Header(default=None, alias="X-Chariot-Conversation"),
) -> Response:
    body = await request.body()
    conversation_id = _validate_conversation_id(x_chariot_conversation)
    return await Agent.current().handle(
        body,
        session=session,
        conversation_id=conversation_id,
    )


def _validate_conversation_id(raw: str | None) -> str | None:
    """空 header → None;非空 → 必须匹配 ULID 正则,否则 400 invalid_conversation_id。"""
    if raw is None or raw == "":
        return None
    if not _ULID_RE.fullmatch(raw):
        raise ServiceError(
            status=400,
            code="invalid_conversation_id",
            message=(f"X-Chariot-Conversation 必须是 26 字符 ULID(`[0-9A-Z]{{26}}`),得到 {raw!r}"),
        )
    return raw
