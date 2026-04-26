"""/v1/* 数据面路由。

0.2.0 起 chariot 只对外暴露 `POST /v1/messages`(Anthropic Messages 协议);
OpenAI 客户端通过外部转换器(LiteLLM / claude-code-router 等)接入,本服务
不做协议翻译。详见 `docs/DESIGN.md` §3。

route 是哑管道:读 body + 调 `Agent.current().handle(body)`,不做 selector /
forwarder / translation —— 这些概念在 chariot 架构(server 自己就是 agent)里
都不存在。
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
