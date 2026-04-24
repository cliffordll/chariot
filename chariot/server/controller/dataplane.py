"""/v1/* 数据面路由。

三端点形状对称,只差 `request_protocol` 一个参数。routes 是哑管道:读 body
+ 调 `agent.handle(protocol, body)`,不做 selector / forwarder / translation
—— 这些概念在 v0 架构(server 自己就是 agent)里都不存在。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from chariot.server.agent import Agent
from chariot.shared.protocols import Protocol

router = APIRouter()


@router.post("/v1/messages")
async def messages(request: Request) -> Response:
    body = await request.body()
    return await Agent.current().handle(Protocol.MESSAGES, body)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.body()
    return await Agent.current().handle(Protocol.CHAT_COMPLETIONS, body)


@router.post("/v1/responses")
async def responses_endpoint(request: Request) -> Response:
    body = await request.body()
    return await Agent.current().handle(Protocol.RESPONSES, body)
