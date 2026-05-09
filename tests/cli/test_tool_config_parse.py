from __future__ import annotations

from chariot.cli.commands.tool import _parse_kv, _parse_option_value


def test_parse_option_value_prefers_json_array() -> None:
    assert _parse_option_value('["example.com","openai.com"]') == [
        "example.com",
        "openai.com",
    ]


def test_parse_option_value_accepts_bareword_array() -> None:
    assert _parse_option_value("[example.com, openai.com]") == [
        "example.com",
        "openai.com",
    ]


def test_parse_option_value_accepts_empty_array() -> None:
    assert _parse_option_value("[]") == []


def test_parse_kv_uses_array_fallback() -> None:
    assert _parse_kv(["allowed_domains=[example.com]"]) == {
        "allowed_domains": ["example.com"]
    }
