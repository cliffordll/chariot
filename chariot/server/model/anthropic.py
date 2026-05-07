"""`AnthropicModel` —— 透传到 Anthropic Messages API 的真实模型实现。

0.2.0 起 chariot 单协议化,本 model 把客户端的 `/v1/messages` 请求经 httpx
转发到上游 `<base_url>/v1/messages`,响应直接透传回去。chariot 自己只做两件事:
1. 用配置里的 `model` 改写 body.model(client 写啥都按配置走)
2. 注入 `x-api-key` / `anthropic-version` header

错误映射(非流 + 流首响应已收前同等行为)
-------------------------------------
- 上游 401 / 403 → 502(用户配置错,但本服务对上游不可用)
- 上游 429 → 透传 429(rate limit)
- 上游 4xx (其它) → 透传(让客户端拿到原始错误)
- 上游 5xx → 502
- httpx 网络异常(超时 / 连接拒绝) → 502 + error 文案带原因

流式中途断开
------------
首响应 200 已发回客户端后,后续上游异常 / 客户端断开 → 只能断 TCP,不伪造
SSE 事件、不重写状态码(沿用 0.1.0 流式契约)。

封装:client / model 都在实例字段里,模块级零自由函数。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, ClassVar, cast

import httpx
from fastapi.responses import Response, StreamingResponse

from chariot.agent.config import ConfigError
from chariot.server.model.base import Model
from chariot.server.service.exceptions import ServiceError

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicModel(Model):
    """走 Anthropic Messages API 的 Model 实现。

    构造方式只能走 `from_config(options)`(由 ModelRegistry 调);手工构造
    `__init__` 也允许,主要给测试 inject 自定义 httpx client 用。
    """

    name: str = "anthropic"

    # 类级超时常量,测试可 monkeypatch
    _CONNECT_TIMEOUT_SEC: ClassVar[float] = 10.0
    _READ_TIMEOUT_SEC: ClassVar[float] = 300.0
    _WRITE_TIMEOUT_SEC: ClassVar[float] = 30.0
    _POOL_TIMEOUT_SEC: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """构造 AnthropicModel。

        - `client=None` → 自建 `httpx.AsyncClient`(生产路径);构造方负责后续 close
          (这里没暴露 close —— 进程级单例,server lifespan 退出时随 GC 释放)
        - `client=<注入>`:测试用,可塞 MockTransport
        """
        self._model = model
        if client is not None:
            self._client = client
            return
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=self._CONNECT_TIMEOUT_SEC,
                read=self._READ_TIMEOUT_SEC,
                write=self._WRITE_TIMEOUT_SEC,
                pool=self._POOL_TIMEOUT_SEC,
            ),
        )

    # ---- ModelRegistry 构造契约 ----

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> AnthropicModel:
        """从 config options 构造;两条来源取 api_key,缺关键字段 raise ConfigError。

        api_key 来源(优先级):
        1. `options["api_key"]` —— 直接写在配置文件里(便捷,但密钥落地)
        2. `options["api_key_env"]`(默认 `ANTHROPIC_API_KEY`)指向的环境变量
        两者都给 → 1 优先;都没拿到非空值 → ConfigError。
        """
        model = options.get("model")
        if not isinstance(model, str) or not model:
            raise ConfigError("anthropic model 配置缺少 'model' 或类型不对")

        api_key = cls._resolve_api_key(options)

        base_url = options.get("base_url", _DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url:
            raise ConfigError("'base_url' 必须是非空字符串")

        return cls(api_key=api_key, model=model, base_url=base_url)

    @staticmethod
    def _resolve_api_key(options: dict[str, Any]) -> str:
        """按优先级 (inline api_key) → (api_key_env 指向的 env) 解析 key。"""
        inline = options.get("api_key")
        if inline is not None:
            if not isinstance(inline, str) or not inline:
                raise ConfigError("'api_key' 必须是非空字符串(留空请整条删掉)")
            return inline

        env_name = options.get("api_key_env", _DEFAULT_API_KEY_ENV)
        if not isinstance(env_name, str) or not env_name:
            raise ConfigError("'api_key_env' 必须是非空字符串")
        env_value = os.environ.get(env_name)
        if not env_value:
            raise ConfigError(f"未拿到 api_key:配置里没填 'api_key',且环境变量 {env_name} 也未设置")
        return env_value

    # ---- Model 接口 ----

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        payload = self._rewrite_for_upstream(body, stream=stream)
        if stream:
            return await self._stream(payload)
        return await self._unary(payload)

    # ---- 内部:body 改写 + 上游调用 ----

    def _rewrite_for_upstream(self, body: bytes, *, stream: bool) -> bytes:
        """改写 body 字段:
        - `model` → 配置里的真实 model name(client 写啥都按配置走)
        - `stream` → 同步本地 stream 参数(Anthropic 据 body.stream 决定输出格式;
          slow path 内部循环以 stream=False 调时,client 原 body 里的 stream=true
          必须被推平,否则上游返回 SSE 而我们走 _unary 拿原始 bytes,JSON 解析炸)

        非法 JSON 直接 raise 400 给客户端。
        """
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ServiceError(
                status=400, code="invalid_json_body", message=f"非法 JSON: {e}"
            ) from e
        if not isinstance(data, dict):
            raise ServiceError(status=400, code="invalid_json_body", message="顶层必须是对象")
        body_dict = cast(dict[str, Any], data)
        body_dict["model"] = self._model
        body_dict["stream"] = stream
        return json.dumps(body_dict, ensure_ascii=False).encode("utf-8")

    async def _unary(self, payload: bytes) -> Response:
        """非流式上游调用。"""
        try:
            upstream = await self._client.post("/v1/messages", content=payload)
        except httpx.TimeoutException as e:
            raise ServiceError(status=502, code="upstream_timeout", message=f"上游超时: {e}") from e
        except httpx.HTTPError as e:
            raise ServiceError(
                status=502, code="upstream_unreachable", message=f"上游不可达: {e}"
            ) from e
        return self._map_upstream_response(upstream)

    async def _stream(self, payload: bytes) -> Response:
        """流式上游调用。

        - 首响应未到 / 错误码:同 _unary 路径处理(可正常 raise / 透传)
        - 首响应 2xx:返回 `StreamingResponse`,字节透传;之后任何异常只能断 TCP
        """
        req = self._client.build_request("POST", "/v1/messages", content=payload)
        try:
            upstream = await self._client.send(req, stream=True)
        except httpx.TimeoutException as e:
            raise ServiceError(status=502, code="upstream_timeout", message=f"上游超时: {e}") from e
        except httpx.HTTPError as e:
            raise ServiceError(
                status=502, code="upstream_unreachable", message=f"上游不可达: {e}"
            ) from e

        sc = upstream.status_code
        if sc != 200:
            # 错误码:把 body 读完 + 关连接,再走 _map(带 raise / 透传)
            try:
                await upstream.aread()
            finally:
                await upstream.aclose()
            return self._map_upstream_response(upstream)

        # 2xx:把 raw 字节流喂给 StreamingResponse;迭代器负责 close
        media_type = upstream.headers.get("content-type", "text/event-stream")
        return StreamingResponse(
            self._stream_chunks(upstream),
            status_code=200,
            media_type=media_type,
        )

    @staticmethod
    async def _stream_chunks(upstream: httpx.Response) -> AsyncIterator[bytes]:
        """逐 chunk 转发上游字节;结束 / 异常时关 response 释放连接。

        异常不吞 —— 让 StreamingResponse 异常上抛,starlette 会断 TCP 给客户端
        (符合 0.1.0 "200 已发后只能断"契约,不伪造事件)。
        """
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    @staticmethod
    def _map_upstream_response(upstream: httpx.Response) -> Response:
        """把上游响应映射成 chariot 对客户端的响应。

        - 401 / 403 → 502(用户配置错,本服务对上游不可用)
        - 429 → 透传(rate limit 直接告诉客户端)
        - 5xx → 502
        - 其它 4xx → 透传 body
        - 2xx → 透传 body
        """
        sc = upstream.status_code
        body = upstream.content
        if sc in (401, 403):
            raise ServiceError(
                status=502,
                code="upstream_auth_failed",
                message=f"上游认证失败({sc});检查 config 里的 api_key / api_key_env",
            )
        if 500 <= sc < 600:
            raise ServiceError(
                status=502,
                code="upstream_server_error",
                message=f"上游 {sc}: {body[:200]!r}",
            )
        # 200~299 / 429 / 其它 4xx 都透传
        return Response(
            content=body,
            status_code=sc,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
