"""Tests for GitStatusTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.git_status import GitStatusTool


def _make_tool() -> GitStatusTool:
    return GitStatusTool.create(ToolEntry(name="git_status", type="git_status", enabled=True, options={}))


@pytest.mark.asyncio
async def test_git_status_no_git_repo(tmp_path: Path) -> None:
    tool = _make_tool()
    result = await tool.execute({"path": str(tmp_path)})
    assert result.get("is_error")
    assert "未找到 git 仓库" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_git_status_clean_repo(tmp_path: Path) -> None:
    import os
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    # 创建一个初始 commit,否则 HEAD 不存在
    (tmp_path / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-gpg-sign"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=env,
    )
    tool = _make_tool()
    result = await tool.execute({"path": str(tmp_path)})
    assert not result.get("is_error")
    assert "分支:" in result["content"][0]["text"]
