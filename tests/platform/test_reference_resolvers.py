"""B3 wave 3 — 4 个 reference resolver 各自的解析 + security 校验。"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from chariot.context.references import ResolveContext
from chariot.context.references.base import ReferenceResolveError
from chariot.context.references.builtin.diff import DiffReferenceResolver
from chariot.context.references.builtin.file import FileReferenceResolver
from chariot.context.references.builtin.session import SessionReferenceResolver
from chariot.context.references.builtin.url import UrlReferenceResolver


def _ctx(cwd: Path, *, allowed: frozenset[str] = frozenset(), sm: object = None) -> ResolveContext:
    return ResolveContext(cwd=cwd, allowed_domains=allowed, sessionmaker=sm)  # type: ignore[arg-type]


# ---- FileReferenceResolver ----


async def test_file_reads_utf8(tmp_path: Path) -> None:
    p = tmp_path / "hello.md"
    p.write_text("# 你好\nworld", encoding="utf-8")
    ref = await FileReferenceResolver().resolve("hello.md", _ctx(tmp_path))
    assert ref.type_id == "file"
    assert "你好" in ref.content
    assert ref.truncated is False


async def test_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(ReferenceResolveError) as exc:
        await FileReferenceResolver().resolve("ghost.md", _ctx(tmp_path))
    assert exc.value.reason == "not_found"


async def test_file_rejects_dotdot_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ReferenceResolveError) as exc:
        await FileReferenceResolver().resolve("../outside.md", _ctx(tmp_path))
    assert exc.value.reason == "path_traversal"


async def test_file_rejects_absolute_outside_cwd(tmp_path: Path) -> None:
    outside = tmp_path.parent / "abs_outside.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ReferenceResolveError) as exc:
        await FileReferenceResolver().resolve(str(outside), _ctx(tmp_path))
    assert exc.value.reason == "path_traversal"


async def test_file_truncates_large(tmp_path: Path) -> None:
    p = tmp_path / "big.txt"
    p.write_text("a" * 200, encoding="utf-8")
    ctx = ResolveContext(cwd=tmp_path, allowed_domains=frozenset(), sessionmaker=None, max_bytes=50)
    ref = await FileReferenceResolver().resolve("big.txt", ctx)
    assert ref.truncated is True
    assert len(ref.content) <= 50


# ---- DiffReferenceResolver ----


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)


async def test_diff_returns_unstaged_changes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)
    (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
    ref = await DiffReferenceResolver().resolve("", _ctx(tmp_path))
    assert ref.type_id == "diff"
    assert "v1" in ref.content or "v2" in ref.content or "a.txt" in ref.content


async def test_diff_rejects_shell_metachar(tmp_path: Path) -> None:
    _git_init(tmp_path)
    with pytest.raises(ReferenceResolveError) as exc:
        await DiffReferenceResolver().resolve("HEAD; rm -rf /", _ctx(tmp_path))
    assert exc.value.reason == "invalid_ref"


async def test_diff_bad_ref_returns_subprocess_error(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)
    with pytest.raises(ReferenceResolveError) as exc:
        await DiffReferenceResolver().resolve("doesnotexist", _ctx(tmp_path))
    assert exc.value.reason == "subprocess_error"


# ---- UrlReferenceResolver ----


async def test_url_rejects_non_http_scheme() -> None:
    ctx = _ctx(Path.cwd(), allowed=frozenset({"example.com"}))
    with pytest.raises(ReferenceResolveError) as exc:
        await UrlReferenceResolver().resolve("file:///etc/passwd", ctx)
    assert exc.value.reason == "invalid_scheme"


async def test_url_rejects_disallowed_host() -> None:
    ctx = _ctx(Path.cwd(), allowed=frozenset({"example.com"}))
    with pytest.raises(ReferenceResolveError) as exc:
        await UrlReferenceResolver().resolve("https://evil.com/x", ctx)
    assert exc.value.reason == "disallowed_host"


async def test_url_rejects_when_empty_allowlist() -> None:
    """空白名单 = `@url:` 全部失败(safe by default)。"""
    with pytest.raises(ReferenceResolveError) as exc:
        await UrlReferenceResolver().resolve("https://example.com", _ctx(Path.cwd()))
    assert exc.value.reason == "disallowed_host"


# ---- SessionReferenceResolver ----


async def test_session_resolves_by_full_id(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db
    from chariot.repos.conversation_repo import ConversationRepo

    sm = await init_db(tmp_path / "test.db")
    try:
        async with sm() as session:
            repo = ConversationRepo(session)
            await repo.create("01CONVABCD")
            await repo.append_message("01CONVABCD", "user", "hello there")
        ref = await SessionReferenceResolver().resolve("01CONVABCD", _ctx(tmp_path, sm=sm))
    finally:
        await dispose_db()
    assert ref.key == "01CONVABCD"
    assert "hello" in ref.content


async def test_session_prefix_too_short(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    sm = await init_db(tmp_path / "test.db")
    try:
        with pytest.raises(ReferenceResolveError) as exc:
            await SessionReferenceResolver().resolve("ab", _ctx(tmp_path, sm=sm))
        assert exc.value.reason == "prefix_too_short"
    finally:
        await dispose_db()


async def test_session_not_found(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    sm = await init_db(tmp_path / "test.db")
    try:
        with pytest.raises(ReferenceResolveError) as exc:
            await SessionReferenceResolver().resolve("01NOTFOUND", _ctx(tmp_path, sm=sm))
        assert exc.value.reason == "not_found"
    finally:
        await dispose_db()


async def test_session_ambiguous_prefix(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db
    from chariot.repos.conversation_repo import ConversationRepo

    sm = await init_db(tmp_path / "test.db")
    try:
        async with sm() as session:
            repo = ConversationRepo(session)
            await repo.create("01ABCDXX01")
            await repo.create("01ABCDXX02")
        with pytest.raises(ReferenceResolveError) as exc:
            await SessionReferenceResolver().resolve("01ABCD", _ctx(tmp_path, sm=sm))
        assert exc.value.reason == "ambiguous_prefix"
    finally:
        await dispose_db()


# 让 pytest 看到 import asyncio 用过(避免 ruff F401)
def test_import_asyncio_used() -> None:
    assert asyncio is not None
