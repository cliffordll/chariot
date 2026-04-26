"""ChariotConfig + ConfigLoader 测试。

覆盖:
- ChariotConfig.empty() / active_entry() / from_dict 正常路径
- from_dict 各种校验错误(重复 name / 缺字段 / active 未知 / 类型错)
- ConfigLoader 文件不存在 → empty;有效文件 → 解析
- ConfigLoader 路径优先级:explicit > env > default
- TOML 语法错误 → ConfigError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.server.config import ChariotConfig, ConfigError, ConfigLoader, ModelEntry

# ---------- ChariotConfig.empty / active_entry ----------


def test_empty_config_via_classmethod() -> None:
    c = ChariotConfig.empty()
    assert c.is_empty()
    assert c.models == ()
    assert c.active is None
    assert c.active_entry() is None


def test_active_entry_returns_matching_entry() -> None:
    e1 = ModelEntry(name="a", type="mock", options={})
    e2 = ModelEntry(name="b", type="anthropic", options={"model_id": "x"})
    c = ChariotConfig(models=(e1, e2), active="b")
    assert c.active_entry() is e2


def test_active_entry_runtime_inconsistency_raises() -> None:
    """绕过 from_dict 直接构造一个 active 指向未知 name 的实例 —— 一致性兜底。"""
    c = ChariotConfig(models=(ModelEntry(name="a", type="mock", options={}),), active="ghost")
    with pytest.raises(ConfigError, match="active"):
        c.active_entry()


# ---------- from_dict 正常路径 ----------


def test_from_dict_minimal_valid() -> None:
    raw = {
        "models": [
            {
                "name": "claude",
                "type": "anthropic",
                "options": {"model_id": "claude-opus-4-5"},
            },
        ],
    }
    c = ChariotConfig.from_dict(raw)
    assert len(c.models) == 1
    assert c.models[0].name == "claude"
    assert c.models[0].type == "anthropic"
    assert c.models[0].options == {"model_id": "claude-opus-4-5"}
    assert c.active is None


def test_from_dict_with_active() -> None:
    raw = {
        "models": [
            {"name": "m1", "type": "mock"},
            {"name": "m2", "type": "anthropic", "options": {"model_id": "x"}},
        ],
        "active": {"model": "m2"},
    }
    c = ChariotConfig.from_dict(raw)
    assert c.active == "m2"
    assert c.active_entry() is not None
    entry = c.active_entry()
    assert entry is not None
    assert entry.name == "m2"


def test_from_dict_options_default_empty() -> None:
    raw = {"models": [{"name": "m", "type": "mock"}]}
    c = ChariotConfig.from_dict(raw)
    assert c.models[0].options == {}


def test_from_dict_no_models_section_returns_empty() -> None:
    """没 [[models]] 也合法 —— 等价于 empty()。"""
    c = ChariotConfig.from_dict({})
    assert c.is_empty()


# ---------- from_dict 校验错误 ----------


def test_from_dict_models_not_list_raises() -> None:
    with pytest.raises(ConfigError, match="models"):
        ChariotConfig.from_dict({"models": "not a list"})


def test_from_dict_entry_not_table_raises() -> None:
    with pytest.raises(ConfigError, match="必须是 table"):
        ChariotConfig.from_dict({"models": ["string-not-table"]})


def test_from_dict_missing_name_raises() -> None:
    with pytest.raises(ConfigError, match="name"):
        ChariotConfig.from_dict({"models": [{"type": "mock"}]})


def test_from_dict_empty_name_raises() -> None:
    with pytest.raises(ConfigError, match="name"):
        ChariotConfig.from_dict({"models": [{"name": "", "type": "mock"}]})


def test_from_dict_missing_type_raises() -> None:
    with pytest.raises(ConfigError, match="type"):
        ChariotConfig.from_dict({"models": [{"name": "x"}]})


def test_from_dict_duplicate_names_raises() -> None:
    raw = {
        "models": [
            {"name": "dup", "type": "mock"},
            {"name": "dup", "type": "anthropic"},
        ],
    }
    with pytest.raises(ConfigError, match="重复"):
        ChariotConfig.from_dict(raw)


def test_from_dict_options_not_table_raises() -> None:
    raw = {"models": [{"name": "m", "type": "mock", "options": "not a table"}]}
    with pytest.raises(ConfigError, match="options"):
        ChariotConfig.from_dict(raw)


def test_from_dict_active_unknown_name_raises() -> None:
    raw = {
        "models": [{"name": "m1", "type": "mock"}],
        "active": {"model": "ghost"},
    }
    with pytest.raises(ConfigError, match="ghost"):
        ChariotConfig.from_dict(raw)


def test_from_dict_active_not_table_raises() -> None:
    raw = {"models": [{"name": "m1", "type": "mock"}], "active": "not-a-table"}
    with pytest.raises(ConfigError, match="active"):
        ChariotConfig.from_dict(raw)


# ---------- ConfigLoader ----------


def test_loader_no_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.toml"
    c = ConfigLoader.load(missing)
    assert c.is_empty()


def test_loader_explicit_path(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[models]]\nname = "m"\ntype = "mock"\n\n[active]\nmodel = "m"\n',
        encoding="utf-8",
    )
    c = ConfigLoader.load(cfg)
    assert c.active == "m"
    assert len(c.models) == 1
    assert c.models[0].name == "m"


def test_loader_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "via-env.toml"
    cfg.write_text('[[models]]\nname = "from_env"\ntype = "mock"\n', encoding="utf-8")
    monkeypatch.setenv("CHARIOT_CONFIG", str(cfg))

    c = ConfigLoader.load()  # 不传 path,走 env
    assert len(c.models) == 1
    assert c.models[0].name == "from_env"


def test_loader_explicit_path_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 path 参数优先级高于环境变量。"""
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text('[[models]]\nname = "from_env"\ntype = "mock"\n', encoding="utf-8")
    explicit_cfg = tmp_path / "explicit.toml"
    explicit_cfg.write_text('[[models]]\nname = "from_explicit"\ntype = "mock"\n', encoding="utf-8")
    monkeypatch.setenv("CHARIOT_CONFIG", str(env_cfg))

    c = ConfigLoader.load(explicit_cfg)
    assert c.models[0].name == "from_explicit"


def test_loader_invalid_toml_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.toml"
    cfg.write_text("this is = not [valid toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML"):
        ConfigLoader.load(cfg)


def test_loader_validation_error_propagates(tmp_path: Path) -> None:
    """合法 TOML 但内容校验失败 —— ConfigError 透传。"""
    cfg = tmp_path / "bad-content.toml"
    cfg.write_text(
        '[[models]]\nname = "m"\ntype = "mock"\n\n[active]\nmodel = "ghost"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="ghost"):
        ConfigLoader.load(cfg)


def test_loader_default_path_is_home_chariot_config_toml() -> None:
    """sanity:默认路径在 ~/.chariot/config.toml,不是别的什么。"""
    assert ConfigLoader.DEFAULT_PATH.name == "config.toml"
    assert ConfigLoader.DEFAULT_PATH.parent.name == ".chariot"
