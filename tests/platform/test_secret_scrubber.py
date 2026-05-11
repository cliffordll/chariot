"""B7 wave 1 —— SecretScrubber 正则集合 + blocks 递归扫除。"""

from __future__ import annotations

from chariot.rl.scrubber import NullScrubber, SecretScrubber


# ---- 字符串扫除 ----


def test_scrub_api_key_sk() -> None:
    s = SecretScrubber()
    assert "<REDACTED>" in s.scrub("export OPENAI=sk-abcdef1234567890abcdef")
    assert "sk-abcdef" not in s.scrub("export OPENAI=sk-abcdef1234567890abcdef")


def test_scrub_aws_access_key() -> None:
    s = SecretScrubber()
    out = s.scrub("AKIA1234567890ABCDEF")
    assert "<REDACTED>" in out
    assert "AKIA" not in out


def test_scrub_bearer_token() -> None:
    s = SecretScrubber()
    out = s.scrub("Authorization: Bearer abcdef1234567890ABCDEF.foo-bar")
    assert "<REDACTED>" in out


def test_scrub_private_key() -> None:
    s = SecretScrubber()
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAA==\n-----END RSA PRIVATE KEY-----"
    out = s.scrub(pem)
    assert out == "<REDACTED>"


def test_scrub_github_pat() -> None:
    s = SecretScrubber()
    out = s.scrub("token=ghp_abcdef1234567890ABCDEF1234567890abcd")
    assert "<REDACTED>" in out


def test_scrub_normal_text_unchanged() -> None:
    """普通文本 / 短字符串不动。"""
    s = SecretScrubber()
    assert s.scrub("hello world") == "hello world"
    assert s.scrub("sk-too-short") == "sk-too-short"  # 长度 < 20 → 不匹配
    assert s.scrub("") == ""


def test_scrub_idempotent() -> None:
    """scrub(scrub(x)) == scrub(x)(REDACTED 不会再被替换)。"""
    s = SecretScrubber()
    once = s.scrub("API_KEY=sk-abcdef1234567890abcdef")
    twice = s.scrub(once)
    assert once == twice


# ---- blocks 递归扫除 ----


def test_scrub_blocks_text() -> None:
    s = SecretScrubber()
    out = s.scrub_blocks([{"type": "text", "text": "key=sk-abcdef1234567890abcdef"}])
    assert "<REDACTED>" in out[0]["text"]


def test_scrub_blocks_tool_use_input() -> None:
    s = SecretScrubber()
    out = s.scrub_blocks(
        [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "shell",
                "input": {"command": "export TOK=sk-abcdef1234567890abcdef"},
            }
        ]
    )
    assert "<REDACTED>" in out[0]["input"]["command"]


def test_scrub_blocks_tool_result_string_content() -> None:
    s = SecretScrubber()
    out = s.scrub_blocks(
        [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "found AKIA1234567890ABCDEF in logs",
            }
        ]
    )
    assert "<REDACTED>" in out[0]["content"]


def test_scrub_blocks_tool_result_nested_blocks() -> None:
    s = SecretScrubber()
    out = s.scrub_blocks(
        [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": [{"type": "text", "text": "leak=sk-abcdef1234567890abcdef"}],
            }
        ]
    )
    assert "<REDACTED>" in out[0]["content"][0]["text"]


# ---- NullScrubber(--raw 模式) ----


def test_null_scrubber_passes_through() -> None:
    n = NullScrubber()
    raw = "sk-abcdef1234567890abcdef"
    assert n.scrub(raw) == raw
    assert n.scrub_blocks([{"type": "text", "text": raw}])[0]["text"] == raw
