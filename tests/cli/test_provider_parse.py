from __future__ import annotations

import pytest

from chariot.cli import render
from chariot.cli.commands.provider import _parse_kv


def test_parse_kv_accepts_key_value_form() -> None:
    assert _parse_kv(["model=qwen2.5:1.5b", "base_url=http://127.0.0.1:52806"], label="-o") == {
        "model": "qwen2.5:1.5b",
        "base_url": "http://127.0.0.1:52806",
    }


def test_parse_kv_accepts_legacy_colon_comma_form() -> None:
    assert _parse_kv(
        ["model:qwen2.5:1.5b,base_url:http://127.0.0.1:52806,api_key:EMPTY"],
        label="-o",
    ) == {
        "model": "qwen2.5:1.5b",
        "base_url": "http://127.0.0.1:52806",
        "api_key": "EMPTY",
    }


def test_parse_kv_reports_template_on_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_die(msg: str) -> None:
        seen.append(msg)
        raise RuntimeError(msg)

    monkeypatch.setattr(render.Renderer, "die", fake_die)

    with pytest.raises(RuntimeError):
        _parse_kv(["broken-input"], label="-o")

    assert seen
    assert "template:" in seen[0]
    assert "compat:" in seen[0]


def test_parse_kv_rejects_json_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_die(msg: str) -> None:
        seen.append(msg)
        raise RuntimeError(msg)

    monkeypatch.setattr(render.Renderer, "die", fake_die)

    with pytest.raises(RuntimeError):
        _parse_kv(
            ['{"model":"qwen2.5:1.5b","base_url":"http://127.0.0.1:52806","api_key":"EMPTY"}'],
            label="-o",
        )

    assert seen
    assert "do not pass a whole JSON blob" in seen[0]
