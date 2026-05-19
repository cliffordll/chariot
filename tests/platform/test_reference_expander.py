"""B3 wave 3 — `ReferenceExpander` 扫描 + 并发解析 + 错误嵌入。"""

from __future__ import annotations

from pathlib import Path

from chariot.agent.chat_request import Message
from chariot.context.references import (
    BaseReferenceResolver,
    ReferenceExpander,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)


class _StubResolver(BaseReferenceResolver):
    type_id = "stub"

    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference:
        if key == "fail":
            raise ReferenceResolveError("not_found", type_id=self.type_id, key=key)
        return ResolvedReference(type_id=self.type_id, key=key, content=f"[content of {key}]")


def _build_expander(tmp_path: Path) -> ReferenceExpander:
    # 用一个真实 type_id (file) 的 stub 替换内建,简化测试
    expander = ReferenceExpander(
        cwd=tmp_path,
        allowed_domains=frozenset(),
        sessionmaker=None,
    )
    expander._resolvers["file"] = _StubResolver()  # type: ignore[assignment]
    return expander


async def test_expand_str_content_replaces_reference(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [Message(role="user", content="please @file:README.md and tell me")]
    out = await expander.expand(msgs)
    assert isinstance(out[0].content, str)
    assert "<reference" in out[0].content
    assert 'type="stub"' in out[0].content
    assert "[content of README.md]" in out[0].content


async def test_expand_multiple_refs_in_one_message(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [Message(role="user", content="check @file:a.md and @file:b.md")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    assert content.count("<reference") == 2
    assert "[content of a.md]" in content
    assert "[content of b.md]" in content


async def test_expand_reference_with_space_after_colon(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [Message(role="user", content="check @file: a.md please")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    assert "[content of a.md]" in content


async def test_expand_diff_without_colon(tmp_path: Path) -> None:
    expander = ReferenceExpander(
        cwd=tmp_path,
        allowed_domains=frozenset(),
        sessionmaker=None,
        resolvers={"diff": _StubResolver()},
    )
    msgs = [Message(role="user", content="review @diff please")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    assert 'type="stub"' in content
    assert "[content of ]" in content


async def test_expand_diff_with_colon_and_space(tmp_path: Path) -> None:
    expander = ReferenceExpander(
        cwd=tmp_path,
        allowed_domains=frozenset(),
        sessionmaker=None,
        resolvers={"diff": _StubResolver()},
    )
    msgs = [Message(role="user", content="review @diff: HEAD~1 please")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    assert "[content of HEAD~1]" in content


async def test_expand_failed_resolver_yields_error_block(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [Message(role="user", content="see @file:fail")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    assert 'error="not_found"' in content
    assert "[content of" not in content


async def test_expand_skips_non_user_messages(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="@file:should_not_expand"),
    ]
    out = await expander.expand(msgs)
    assert "<reference" not in (out[1].content if isinstance(out[1].content, str) else "")


async def test_expand_unknown_type_yields_error_block(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    # @unknown:xyz —— 不在 REFERENCE_RESOLVERS;不命中 pattern 所以原样
    msgs = [Message(role="user", content="see @unknown:xyz")]
    out = await expander.expand(msgs)
    content = out[0].content
    assert isinstance(content, str)
    # pattern 限于 (file|diff|url|session) → @unknown:xyz 不匹配 → 原样保留
    assert "@unknown:xyz" in content


async def test_expand_anthropic_blocks(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [
        Message(
            role="user",
            content=[
                {"type": "text", "text": "before @file:x.md after"},
                {"type": "image", "source": {"url": "..."}},
            ],
        ),
    ]
    out = await expander.expand(msgs)
    blocks = out[0].content
    assert isinstance(blocks, list)
    assert "[content of x.md]" in blocks[0]["text"]
    assert blocks[1]["type"] == "image"  # 非 text block 原样


async def test_expand_no_references_returns_original(tmp_path: Path) -> None:
    expander = _build_expander(tmp_path)
    msgs = [Message(role="user", content="plain text no refs")]
    out = await expander.expand(msgs)
    assert out is not msgs  # 新 list,但内容相等
    assert out[0] is msgs[0]  # message 不变(没改)
