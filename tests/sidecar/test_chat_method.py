"""sidecar `chat` method 单测(0.6.5 S.8b)。

跟 `tests/rpc/test_jsonrpc.py` 不同:这里测的是"chat method 通过
JsonRpcServer 走完完整 dispatch 路径"的端到端行为,而非单帧解析。

覆盖:
- 流式:N 个 ChatEvent → N 个 chat_event notify + 1 个 response
- params 校验:provider_ref 缺 / messages 缺 / role 非法 / content 类型错 →
  ERR_INVALID_PARAMS
- 可选字段 pass-through:model / conversation_id / max_tokens / system 进 ChatRequest
- response shape:`{stream_id, ended_at}`(stream_id 是 UUID4 hex,ended_at
  是 epoch 浮点秒)
- ChatEvent payload shape:`dataclasses.asdict` 形态(kind / message /
  content_block / delta / index / error_type / error_message / usage)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.database.session import init_db
from chariot.repos.log_repo import LogRepo
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods

# 默认 agent 路径不读 db_path(走 self.agent.run);测试给个常量占位即可
_DUMMY_DB_PATH = Path("/tmp/chariot-test.db")

# ---------------------------------------------------------------------------
# 测试基础设施
# ---------------------------------------------------------------------------


def make_reader(data: bytes) -> asyncio.StreamReader:
    """预填数据 + EOF 的 StreamReader。"""
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
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self.buf.split(b"\n") if s.strip()]


class _MockAgent:
    """`_AgentRunner` Protocol duck-type:run 产预设 ChatEvent 序列,捕获 last_req。"""

    def __init__(self, events: list[ChatEvent]) -> None:
        self.events = list(events)
        self.last_req: ChatRequest | None = None

    async def run_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.last_req = req
        for ev in self.events:
            yield ev


def _chat_request_frame(rid: int, params: dict[str, Any]) -> bytes:
    """拼一帧 chat request(带换行)。"""
    return (json.dumps({"jsonrpc": "2.0", "id": rid, "method": "chat", "params": params}) + "\n").encode()


# ---------------------------------------------------------------------------
# 流式行为
# ---------------------------------------------------------------------------


class TestChatStreaming:
    async def test_six_events_to_six_notifies_plus_response(self) -> None:
        """text-only chat:6 个 ChatEvent → 6 个 notify + 1 个 response。"""
        events = [
            ChatEvent.message_start(message_id="m1", model="mock-1"),
            ChatEvent.text_block_start(index=0),
            ChatEvent.text_delta("hi", index=0),
            ChatEvent.block_stop(index=0),
            ChatEvent.message_delta_done(stop_reason="end_turn"),
            ChatEvent.message_done(),
        ]
        agent = _MockAgent(events)
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        params = {"provider_ref": "mock", "messages": [{"role": "user", "content": "hi"}]}
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        notifies = [line for line in lines if "method" in line]
        responses = [line for line in lines if "result" in line]

        assert len(notifies) == 6
        assert len(responses) == 1

        # 全部 notify 都是 chat_event
        assert all(n["method"] == "chat_event" for n in notifies)

        # ChatEvent.kind 顺序对得上(asdict 把 kind 放 params 里)
        kinds = [n["params"]["kind"] for n in notifies]
        assert kinds == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

    async def test_response_carries_stream_id_and_ended_at(self) -> None:
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        params = {
            "provider_ref": "mock",
            "messages": [{"role": "user", "content": "x"}],
        }
        reader = make_reader(_chat_request_frame(7, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        responses = [line for line in writer.lines() if "result" in line]
        assert len(responses) == 1
        result = responses[0]["result"]
        assert isinstance(result["stream_id"], str)
        assert len(result["stream_id"]) == 32  # uuid4 hex
        assert isinstance(result["ended_at"], float)
        assert result["ended_at"] > 0

    async def test_chat_event_payload_uses_claude_naming(self) -> None:
        """notify payload 用 Claude 形态命名(message_start / content_block_*),
        **不**是 chariot 旧式 turn_start / text 等。"""
        events = [ChatEvent.message_start(message_id="m1", model="mock-1")]
        agent = _MockAgent(events)
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        reader = make_reader(
            _chat_request_frame(
                1,
                {"provider_ref": "mock", "messages": [{"role": "user", "content": "x"}]},
            )
        )
        writer = MockWriter()
        await server.serve(reader, writer)

        notifies = [line for line in writer.lines() if "method" in line]
        # message_start payload 含 message dict(Claude 形态)
        assert notifies[0]["params"]["kind"] == "message_start"
        assert "message" in notifies[0]["params"]
        msg = notifies[0]["params"]["message"]
        assert msg["model"] == "mock-1"
        assert msg["role"] == "assistant"

    async def test_chat_writes_log_row(self, tmp_path: Path) -> None:
        events = [
            ChatEvent.message_start(message_id="m1", model="mock-1", usage={"input_tokens": 3}),
            ChatEvent.message_delta_done(stop_reason="end_turn", usage={"output_tokens": 7}),
            ChatEvent.message_done(),
        ]
        agent = _MockAgent(events)
        server = JsonRpcServer()
        db_path = tmp_path / "chariot.db"
        register_methods(server, agent, db_path=db_path)

        params = {"provider_ref": "mock", "messages": [{"role": "user", "content": "hi"}]}
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        sm = await init_db(db_path)
        async with sm() as session:
            logs = await LogRepo(session).list_logs(limit=10, offset=0)

        assert len(logs) == 1
        assert logs[0].provider == "mock"
        assert logs[0].status == "ok"
        assert logs[0].input_tokens == 3
        assert logs[0].output_tokens == 7


# ---------------------------------------------------------------------------
# Params 校验
# ---------------------------------------------------------------------------


class TestParamsValidation:
    async def _send(self, params: dict[str, Any]) -> dict[str, Any]:
        """发一次 chat,返第一帧解析。"""
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)
        return writer.lines()[0]

    async def test_missing_provider_ref(self) -> None:
        line = await self._send({"messages": [{"role": "user", "content": "x"}]})
        # provider_ref 可选;不传时 AIAgent 层报 missing_provider error event
        assert "result" in line
        assert "stream_id" in line["result"]

    async def test_provider_ref_empty_string(self) -> None:
        line = await self._send({"provider_ref": "", "messages": [{"role": "user", "content": "x"}]})
        # 空串视为 null;AIAgent 层报 missing_provider error event
        assert "result" in line
        assert "stream_id" in line["result"]

    async def test_legacy_provider_display_name_param_ignored(self) -> None:
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        legacy_param = "provider_name"
        params = {
            legacy_param: "mock",
            "messages": [{"role": "user", "content": "x"}],
        }
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        captured = agent.last_req
        assert captured is not None
        assert captured.provider_ref is None

    async def test_missing_messages(self) -> None:
        line = await self._send({"provider_ref": "mock"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS
        assert "messages" in line["error"]["message"]

    async def test_messages_empty_list(self) -> None:
        line = await self._send({"provider_ref": "mock", "messages": []})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_message_invalid_role(self) -> None:
        line = await self._send(
            {
                "provider_ref": "mock",
                "messages": [{"role": "system", "content": "x"}],
            }
        )
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS
        assert "role" in line["error"]["message"]

    async def test_message_content_wrong_type(self) -> None:
        line = await self._send(
            {
                "provider_ref": "mock",
                "messages": [{"role": "user", "content": 123}],
            }
        )
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS
        assert "content" in line["error"]["message"]

    async def test_message_not_object(self) -> None:
        line = await self._send(
            {
                "provider_ref": "mock",
                "messages": ["not-a-dict"],
            }
        )
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# 可选字段 pass-through
# ---------------------------------------------------------------------------


class TestOptionalFieldsPassThrough:
    async def test_model_convo_max_tokens_system(self) -> None:
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        params = {
            "provider_ref": "mock",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "claude-haiku-4-5",
            "conversation_id": "01H_TEST",
            "max_tokens": 1024,
            "system": "be nice",
        }
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        captured = agent.last_req
        assert captured is not None
        assert captured.provider_ref == "mock"
        assert captured.model == "claude-haiku-4-5"
        assert captured.conversation_id == "01H_TEST"
        assert captured.max_tokens == 1024
        assert captured.system == "be nice"

    async def test_messages_with_content_blocks(self) -> None:
        """content 是 content block list(图片 / tool_result 等场景)。"""
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        params = {
            "provider_ref": "mock",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ],
        }
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        captured = agent.last_req
        assert captured is not None
        assert isinstance(captured.messages[0].content, list)
        assert captured.messages[0].content == [{"type": "text", "text": "hello"}]

    async def test_unknown_field_in_params_rejected(self) -> None:
        """params 含 ChatRequest 不识别的字段 → TypeError → ERR_INVALID_PARAMS。

        注:_RequestDecoder 只 pass-through 白名单字段,不识别的字段直接丢弃,
        不报错(避免前端字段加新字段时硬中断)。这里测"不在白名单的字段被
        silently 丢弃"。
        """
        agent = _MockAgent([])
        server = JsonRpcServer()
        register_methods(server, agent, db_path=_DUMMY_DB_PATH)

        params = {
            "provider_ref": "mock",
            "messages": [{"role": "user", "content": "hi"}],
            "unknown_field_42": "ignored",
        }
        reader = make_reader(_chat_request_frame(1, params))
        writer = MockWriter()
        await server.serve(reader, writer)

        lines = writer.lines()
        # 不该出错;chat 应正常完成(空 ChatEvent 流 + response)
        responses = [line for line in lines if "result" in line]
        assert len(responses) == 1
        assert "stream_id" in responses[0]["result"]
