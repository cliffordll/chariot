"""JsonRpcServer 单测(0.6.5 S.8a)。

覆盖:
- 帧解析:粘连帧 / 半帧拼接 / 解析错(ERR_PARSE)
- 校验:method 非 str / params 非 object(ERR_INVALID_REQUEST / ERR_INVALID_PARAMS)
- Dispatch:method not found / 正常返 result / RpcError 抛 / 内部异常 → ERR_INTERNAL
- Notify:handler 用 ctx.notify 推 N 帧 + 1 帧 response;notification request
  (无 id)不发 response
- Lifecycle:EOF 优雅 return;serve 之外调 ctx.notify 静默丢弃
- Registration:重复 method 名抛 ValueError

测试基础设施(模块级):
- `make_reader(data)`:asyncio.StreamReader 预填数据 + EOF
- `MockWriter`:asyncio.StreamWriter 兼容的 in-memory 替身;符合 `_Writer`
  Protocol(write + async drain)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError

# ---------------------------------------------------------------------------
# 测试基础设施
# ---------------------------------------------------------------------------


def make_reader(data: bytes) -> asyncio.StreamReader:
    """预填数据 + EOF 的 StreamReader。serve 跑完一轮就会 break。"""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class MockWriter:
    """`_Writer` Protocol 实现:in-memory 收 server 写出的字节。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        # 让出 event loop —— 模拟真 StreamWriter.drain 的 yield 行为,
        # 给 asyncio.Lock 释放机会
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        """把 buf 当 newline-delimited JSON 解出来。"""
        return [json.loads(s) for s in self.buf.split(b"\n") if s.strip()]


# ---------------------------------------------------------------------------
# 帧解析
# ---------------------------------------------------------------------------


class TestFraming:
    async def test_concatenated_frames(self) -> None:
        """两帧粘连(同 readline 调用读出两次)→ 解出两个 response。"""
        server = JsonRpcServer()

        @server.method("ping")
        async def ping(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            return {"pong": True}

        data = (
            b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{}}\n'
        )
        reader = make_reader(data)
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        ids = sorted(line["id"] for line in lines)
        assert ids == [1, 2]
        assert all(line["result"] == {"pong": True} for line in lines)

    async def test_partial_frame_then_completion(self) -> None:
        """半帧 + 后续补全 → 一个完整 frame。"""
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"jsonrpc":"2.0","id":1')

        server = JsonRpcServer()

        @server.method("echo")
        async def echo(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del ctx
            return p

        writer = MockWriter()
        # 起 serve,等它 stuck 在 readline 上
        task = asyncio.create_task(server.serve(reader, writer))
        await asyncio.sleep(0)  # 让 serve 跑到 readline
        reader.feed_data(b',"method":"echo","params":{"x":1}}\n')
        reader.feed_eof()
        await task

        lines = writer.lines()
        assert len(lines) == 1
        assert lines[0]["id"] == 1
        assert lines[0]["result"] == {"x": 1}

    async def test_invalid_json_returns_parse_error(self) -> None:
        server = JsonRpcServer()
        reader = make_reader(b"not json at all\n")
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert len(lines) == 1
        assert lines[0]["id"] is None
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_PARSE


# ---------------------------------------------------------------------------
# Envelope 校验
# ---------------------------------------------------------------------------


class TestEnvelopeValidation:
    async def test_request_not_object_returns_invalid_request(self) -> None:
        """req 是 array → ERR_INVALID_REQUEST(也涵盖 batch 形式拒绝)。"""
        server = JsonRpcServer()
        reader = make_reader(b"[1,2,3]\n")
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_INVALID_REQUEST

    async def test_method_not_string_returns_invalid_request(self) -> None:
        server = JsonRpcServer()
        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":42}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_INVALID_REQUEST
        assert lines[0]["id"] == 1

    async def test_method_empty_string_returns_invalid_request(self) -> None:
        server = JsonRpcServer()
        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":""}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_INVALID_REQUEST

    async def test_params_not_object_returns_invalid_params(self) -> None:
        server = JsonRpcServer()
        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"x","params":[1,2,3]}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_params_missing_treated_as_empty_dict(self) -> None:
        """params 缺失 → handler 收到 `{}`,不报错。"""
        server = JsonRpcServer()
        captured: dict[str, Any] = {}

        @server.method("capture")
        async def capture(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del ctx
            captured["params"] = p
            return {"ok": True}

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"capture"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        assert captured["params"] == {}
        assert writer.lines()[0]["result"] == {"ok": True}


# ---------------------------------------------------------------------------
# Method dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_method_not_found(self) -> None:
        server = JsonRpcServer()
        reader = make_reader(b'{"jsonrpc":"2.0","id":7,"method":"nonexistent"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["id"] == 7
        assert lines[0]["error"]["code"] == JsonRpcServer.ERR_METHOD_NOT_FOUND

    async def test_method_returns_result(self) -> None:
        server = JsonRpcServer()

        @server.method("echo")
        async def echo(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del ctx
            return {"echoed": p.get("msg", "")}

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"echo","params":{"msg":"hi"}}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert lines[0]["result"] == {"echoed": "hi"}

    async def test_method_returns_none_serializes_as_empty_object(self) -> None:
        """handler return None → response.result 是 `{}`(JSON-RPC 必须有 result)。"""
        server = JsonRpcServer()

        @server.method("void")
        async def void(p: dict[str, Any], ctx: RpcContext) -> None:
            del p, ctx
            return None

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"void"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        assert writer.lines()[0]["result"] == {}

    async def test_method_raises_rpc_error_propagates_code(self) -> None:
        server = JsonRpcServer()

        @server.method("boom")
        async def boom(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            raise RpcError(-32099, "custom business error")

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"boom"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        line = writer.lines()[0]
        assert line["error"]["code"] == -32099
        assert line["error"]["message"] == "custom business error"

    async def test_method_raises_generic_exception_returns_internal(self) -> None:
        """非 RpcError 抛异常 → 转 ERR_INTERNAL,**不 leak** 异常 message。"""
        server = JsonRpcServer()

        @server.method("crash")
        async def crash(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            raise RuntimeError("internal detail not for client")

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"crash"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        err = writer.lines()[0]["error"]
        assert err["code"] == JsonRpcServer.ERR_INTERNAL
        # 异常 message 不 leak 给客户端
        assert "internal detail not for client" not in err["message"]
        # 但 type name 给客户端做粗 debug 是 OK 的(对应 hermes 那种习惯)
        assert "RuntimeError" in err["message"]


# ---------------------------------------------------------------------------
# Notify
# ---------------------------------------------------------------------------


class TestNotify:
    async def test_handler_pushes_notify_frames_then_returns(self) -> None:
        """handler 中 await ctx.notify N 次 → reader 收 N 个 notify + 1 个 response。"""
        server = JsonRpcServer()

        @server.method("stream")
        async def stream(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p
            for i in range(3):
                await ctx.notify("event", {"i": i})
            return {"done": True, "count": 3}

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"stream"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        assert len(lines) == 4
        notifies = [line for line in lines if "method" in line]
        responses = [line for line in lines if "result" in line]
        assert len(notifies) == 3
        assert len(responses) == 1

        # Notify 帧形态:无 id,有 method + params
        for i, n in enumerate(notifies):
            assert "id" not in n
            assert n["method"] == "event"
            assert n["params"] == {"i": i}

        # Response 帧
        assert responses[0]["id"] == 1
        assert responses[0]["result"] == {"done": True, "count": 3}

    async def test_notification_request_no_response(self) -> None:
        """request 无 id(notification)→ handler 跑完不发 response。"""
        server = JsonRpcServer()
        called = False

        @server.method("fire_and_forget")
        async def f(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            nonlocal called
            called = True
            return {"ignored": True}

        reader = make_reader(b'{"jsonrpc":"2.0","method":"fire_and_forget"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        assert called
        assert writer.buf == b""  # 没有 response 帧

    async def test_notify_outside_serve_silently_dropped(self) -> None:
        """serve 之外 / shutdown 期间调 ctx.notify → 静默丢(无异常)。"""
        server = JsonRpcServer()
        ctx = RpcContext(server)
        # serve 没启,_writer is None → 静默 return,不抛
        await ctx.notify("event", {"x": 1})


# ---------------------------------------------------------------------------
# Lifecycle:EOF / cancellation
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_eof_graceful_exit(self) -> None:
        """EOF (b'')→ serve 优雅 return,无异常,无输出。"""
        server = JsonRpcServer()
        reader = make_reader(b"")
        writer = MockWriter()
        await server.serve(reader, writer)
        assert writer.buf == b""

    async def test_eof_after_one_request_finishes_response_first(self) -> None:
        """1 帧 + EOF → 跑完 handler、写完 response 才 return(不丢 response)。"""
        server = JsonRpcServer()

        @server.method("slow")
        async def slow(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            await asyncio.sleep(0)  # yield 一次模拟 IO
            return {"ok": True}

        reader = make_reader(b'{"jsonrpc":"2.0","id":1,"method":"slow"}\n')
        writer = MockWriter()
        await server.serve(reader, writer)

        # serve return 时,task 已经 gather 完,response 写出了
        lines = writer.lines()
        assert len(lines) == 1
        assert lines[0]["result"] == {"ok": True}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_duplicate_method_name_raises(self) -> None:
        server = JsonRpcServer()

        @server.method("x")
        async def x1(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            return {}

        with pytest.raises(ValueError, match="duplicate method"):

            @server.method("x")
            async def x2(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
                del p, ctx
                return {}

    def test_known_methods_returns_registered_names(self) -> None:
        server = JsonRpcServer()

        @server.method("a")
        async def a(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            return {}

        @server.method("b")
        async def b(p: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
            del p, ctx
            return {}

        assert server.known_methods() == {"a", "b"}


# ---------------------------------------------------------------------------
# RpcError 类
# ---------------------------------------------------------------------------


class TestRpcError:
    def test_carries_code_and_message(self) -> None:
        e = RpcError(-32099, "biz error")
        assert e.code == -32099
        assert e.message == "biz error"
        assert str(e) == "biz error"

    def test_is_exception_subclass(self) -> None:
        """能被 except Exception 捕获(对照 server 的 fallback 分支)。"""
        try:
            raise RpcError(-1, "x")
        except Exception as e:
            assert isinstance(e, RpcError)
