"""SkillRegistry —— builtin/* YAML + DB skills 表 union 视图(B6 wave 1)。

封装策略(CLAUDE.md ⭐):
- 单一类编排;模块级零自由函数
- `load` 是异步类方法(读 DB 需 await);构造后 registry 不再变(不热重载)
- 同名 DB skill **覆盖** builtin(让用户能 patch 内置 prompt);删 DB 行 → 重新
  暴露 builtin

加载时机:`AIAgent.bootstrap` 阶段调一次,挂 `self._skill_registry`;wave 3 起
`propose_skill` tool 触发 `SkillRepo.create` 之后**不**热重载,提议出的 skill
要 sidecar / CLI 进程重启才生效(跟 provider / tool 同语义)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from chariot.skills.base import BaseSkill, BuiltinSkill, DbSkill
from chariot.skills.loader import SkillLoader

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SkillRegistry:
    """builtin + DB skills 合并视图,name → BaseSkill。"""

    def __init__(self, *, builtin: dict[str, BuiltinSkill], db: dict[str, DbSkill]) -> None:
        # DB 优先覆盖 builtin
        merged: dict[str, BaseSkill] = dict(builtin)
        merged.update(db)
        self._merged = merged
        self._builtin = builtin
        self._db = db

    @classmethod
    async def load(cls, sessionmaker: async_sessionmaker[AsyncSession] | None = None) -> Self:
        """从 builtin YAML + DB 装载;`sessionmaker=None` 时只装 builtin(测试 / no-DB)。"""
        builtin_list = SkillLoader.load_builtin()
        builtin = {s.name: s for s in builtin_list}
        db: dict[str, DbSkill] = {}
        if sessionmaker is not None:
            db = await cls._load_db(sessionmaker)
        return cls(builtin=builtin, db=db)

    @staticmethod
    async def _load_db(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, DbSkill]:
        from chariot.repos.skill_repo import SkillRepo
        from chariot.skills.loader import SkillLoader, SkillManifestError

        out: dict[str, DbSkill] = {}
        async with sessionmaker() as session:
            entries = await SkillRepo(session).list_entries()
        for entry in entries:
            # DB skill 的 content 字段就是 YAML 文本(wave 3 propose 落库时写的)
            # 解析失败 → skip 那条行(不阻其它 skill 装载;留 audit / log 追)
            try:
                manifest = SkillLoader.parse_yaml(entry.content or "", enabled=entry.enabled)
            except SkillManifestError:
                continue
            out[manifest.name] = DbSkill(manifest)
        return out

    # ---- 查询 ----

    def get(self, name: str) -> BaseSkill | None:
        return self._merged.get(name)

    def list_all(self) -> list[BaseSkill]:
        """全部 skill,按 name 字典序。"""
        return sorted(self._merged.values(), key=lambda s: s.name)

    def list_enabled(self) -> list[BaseSkill]:
        """只列 enabled = True 的(builtin 永远 enabled,DB 行看 skills.enabled)。"""
        return [s for s in self.list_all() if s.enabled]

    @property
    def builtin_names(self) -> list[str]:
        return sorted(self._builtin)

    @property
    def db_names(self) -> list[str]:
        return sorted(self._db)
