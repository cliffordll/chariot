"""B7 RL 数据形态(frozen dataclass 集合)。

封装策略(CLAUDE.md ⭐):
- 模块级零自由函数;只放 dataclass 定义,实例化逻辑在 exporter/reward/packager
- frozen=True 让 ExportEntry 可 hash(packager 去重要用)

数据流::

    chariot trace+messages+audit  ←─ TrajectoryExporter ─→ ExportEntry (wave 1)
    ExportEntry ─→ RewardAnnotator ─→ ExportEntry with .reward 填充 (wave 2)
    多条 ExportEntry ─→ DatasetPackager ─→ DatasetEntry JSONL (wave 3)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExportEntry:
    """一次 chat turn 的完整 trajectory 切片。dataset JSONL 一行一个。

    跟 TraceTurn 1:1 对应(turn_id 主键);prompt / response 是 scrubbed 后的
    Anthropic blocks 形态(可直接喂回 chariot 复现 / 给 trainer 用)。
    """

    # 主键 / 定位
    conversation_id: str
    turn_id: str
    sequence: int  # 同 conversation 内 turn 序号(从 0)

    # 模型输入 / 输出(已 scrub)
    prompt: list[dict[str, Any]] = field(default_factory=list)
    response: list[dict[str, Any]] = field(default_factory=list)

    # 副产物
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    provider_call: dict[str, Any] = field(default_factory=dict)
    audit_signals: dict[str, Any] = field(default_factory=dict)

    # wave 2 才填(wave 1 默认 None)
    reward: float | None = None
    reward_breakdown: dict[str, float] | None = None

    # 元数据
    agent_profile: str | None = None
    stop_reason: str | None = None
    error_type: str | None = None
    started_at: str = ""  # ISO8601
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSONL 序列化用。dataclasses.asdict 保留所有字段,缺省的 None 也保留。"""
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class RewardScore:
    """wave 2 RewardAnnotator 给一条 ExportEntry 打的分。

    - `reward`:加权合成最终值,clip 到 [-1.0, +1.0]
    - `static_part`:静态规则部分(0-1)
    - `critic_part`:critic 评分部分(None = 未跑 critic / 退化纯静态)
    - `breakdown`:每条子规则的贡献(debug 用,前端不展示)
    """

    reward: float
    static_part: float
    critic_part: float | None
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetSummary:
    """wave 3 DatasetPackager.pack 的输出摘要。"""

    total: int
    train: int
    eval: int
    mean_reward: float
    reward_buckets: dict[str, int]  # {"pos": N, "zero": N, "neg": N}
    out_path: str
