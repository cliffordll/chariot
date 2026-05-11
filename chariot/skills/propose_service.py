"""SkillProposeService —— skill propose 全链路 orchestrator(B6 wave 3)。

封装策略(CLAUDE.md ⭐):
- 单类编排;模块级零自由函数
- 接 `sessionmaker / audit_hooks / checkpoint_manager`(均可选,缺则降级 no-op)
- guardrail / ApprovalPolicy 在 ToolExecutionService 层已拦,本类**假设** caller
  已 pass 规则(进到 propose() 前 verdict ∈ {ALLOW, REQUIRE_APPROVAL+yolo})
- 任一段失败 → 返 `ProposeResult(ok=False, error=...)`,不抛;audit 失败也吞

接入点(典型):`chariot/tools/builtin/propose_skill.py`(`ProposeSkillTool.execute`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from chariot.agent.exceptions import ConfigError
from chariot.skills.loader import SkillLoader, SkillManifestError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.audit import AuditHookManager
    from chariot.checkpoints import CheckpointManager


@dataclass(frozen=True)
class ProposeInput:
    """propose_skill tool 的入参 → service 内部数据形态。

    跟 `ProposeSkillTool.input_schema` 一一对应;字段约束(长度 / 模式)由 tool
    层 schema 校验 + `SkillLoader` manifest 校验双重保证。
    """

    name: str
    description: str
    prompt: str
    version: str = "0.1.0"
    allowed_tools: list[str] | None = None
    forbidden_tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    proposer: str | None = None  # bookkeeping;wave 3 暂固定 "agent"


@dataclass(frozen=True)
class ProposeResult:
    """全链路单次结果。

    - `ok=True`:三件套(checkpoint / skill row / audit)全写;`skill_id` + `skill_name` 有值
    - `ok=False`:任一段失败;`error` 描述,`checkpoint_id` 若已建则保留(用户可手工
      rollback)。**checkpoint 不自动 cleanup** —— 留给用户决定
    """

    ok: bool
    skill_id: str | None = None
    skill_name: str | None = None
    checkpoint_id: str | None = None
    error: str | None = None


class SkillProposeService:
    """propose_skill 全链路:重名 check → checkpoint → SkillRepo.create → audit。

    用法::

        service = SkillProposeService(
            sessionmaker=sm,
            audit_hooks=hooks,
            checkpoint_manager=mgr,
        )
        result = await service.propose(ProposeInput(name="x", description="...", prompt="..."))
        if result.ok:
            ...
        else:
            ...

    bootstrap 路径:`AIAgent.bootstrap` 构造一个挂 `self._skill_propose`,然后通过
    `ProposeSkillTool.attach_service` 注入到具体 tool 实例。
    """

    CHECKPOINT_NAME_PREFIX = "before-skill-propose-"

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
        audit_hooks: AuditHookManager | None = None,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        from chariot.audit import AuditHookManager as _AuditHookManager

        self._sessionmaker = sessionmaker
        self._audit_hooks = audit_hooks or _AuditHookManager(None)
        self._checkpoint_manager = checkpoint_manager

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession] | None:
        return self._sessionmaker

    @property
    def audit_hooks(self) -> AuditHookManager:
        return self._audit_hooks

    @property
    def checkpoint_manager(self) -> CheckpointManager | None:
        return self._checkpoint_manager

    async def propose(self, input: ProposeInput) -> ProposeResult:
        """跑全链路。任一段失败立即返,不抛。"""
        if self._sessionmaker is None:
            return ProposeResult(
                ok=False,
                skill_name=input.name,
                error="propose 链路未装 sessionmaker(bootstrap 不全)",
            )

        # 1. manifest 自检 —— 把 input 拼成 YAML 文本 + 走 SkillLoader 校验
        try:
            yaml_text, _manifest = self._build_yaml(input)
        except SkillManifestError as e:
            await self._audit_fail(input, checkpoint_id=None, error=str(e))
            return ProposeResult(
                ok=False,
                skill_name=input.name,
                error=f"manifest 校验失败: {e}",
            )

        # 2. 重名 check —— DB 已有同名 skill 直接拒(propose ≠ update)
        try:
            from chariot.repos.skill_repo import SkillRepo

            async with self._sessionmaker() as session:
                existing = await SkillRepo(session).get_by_name(input.name)
            if existing is not None:
                msg = f"skill {input.name!r} already exists(用 update 而非 propose)"
                await self._audit_fail(input, checkpoint_id=None, error=msg)
                return ProposeResult(
                    ok=False,
                    skill_name=input.name,
                    error=msg,
                )
        except Exception as exc:  # pragma: no cover - DB 异常 best-effort
            await self._audit_fail(input, checkpoint_id=None, error=f"重名 check 失败: {exc}")
            return ProposeResult(
                ok=False,
                skill_name=input.name,
                error=f"重名 check 失败: {exc}",
            )

        # 3. checkpoint —— manager 缺 → skip(测试 / no-DB);存在 → 必须成功
        checkpoint_id: str | None = None
        if self._checkpoint_manager is not None:
            try:
                entry = await self._checkpoint_manager.create(f"{self.CHECKPOINT_NAME_PREFIX}{input.name}")
                checkpoint_id = entry.id
            except Exception as exc:
                msg = f"checkpoint 创建失败: {exc}"
                await self._audit_fail(input, checkpoint_id=None, error=msg)
                return ProposeResult(
                    ok=False,
                    skill_name=input.name,
                    error=msg,
                )

        # 4. SkillRepo.create —— content 是 YAML 文本,enabled=False(人工 enable 才生效)
        try:
            from chariot.repos.skill_repo import SkillRepo

            async with self._sessionmaker() as session:
                row = await SkillRepo(session).create(
                    name=input.name,
                    description=input.description,
                    content=yaml_text,
                    enabled=False,
                    meta={
                        "source": "propose",
                        "proposer": input.proposer or "agent",
                        "checkpoint_id": checkpoint_id,
                    },
                )
        except ConfigError as exc:
            # 罕见:重名 check 后被并发 race 抢先 create;按失败处理
            msg = f"skill row 写入失败: {exc}"
            await self._audit_fail(input, checkpoint_id=checkpoint_id, error=msg)
            return ProposeResult(
                ok=False,
                skill_name=input.name,
                checkpoint_id=checkpoint_id,
                error=msg,
            )

        # 5. audit 成功事件
        await self._audit_hooks.record_skill_store(
            skill_id=row.id,
            name=row.name,
            action="create",
            source="propose",
            proposer=input.proposer or "agent",
            checkpoint_id=checkpoint_id,
        )
        return ProposeResult(
            ok=True,
            skill_id=row.id,
            skill_name=row.name,
            checkpoint_id=checkpoint_id,
        )

    # ---- 内部 helpers ----

    @staticmethod
    def _build_yaml(input: ProposeInput) -> tuple[str, object]:
        """把 ProposeInput 拼成 YAML 文本(DB.content 列就存 YAML),并跑 SkillLoader
        校验确认 manifest 合规。

        return: (yaml_text, parsed_manifest)
        """
        manifest_dict: dict[str, object] = {
            "schema_version": 1,
            "name": input.name,
            "version": input.version,
            "description": input.description,
            "prompt": input.prompt,
        }
        if input.allowed_tools is not None:
            manifest_dict["allowed_tools"] = list(input.allowed_tools)
        if input.forbidden_tools:
            manifest_dict["forbidden_tools"] = list(input.forbidden_tools)
        if input.tags:
            manifest_dict["tags"] = list(input.tags)
        yaml_text = yaml.safe_dump(manifest_dict, sort_keys=False, allow_unicode=True)
        # 走 loader 校验一遍,确保字段都过 schema
        manifest = SkillLoader.parse_yaml(yaml_text, enabled=False)
        return yaml_text, manifest

    async def _audit_fail(
        self,
        input: ProposeInput,
        *,
        checkpoint_id: str | None,
        error: str,
    ) -> None:
        """propose 失败时也写一条 audit,让 `chariot skill proposals` 能看见。

        约定:用 `EVENT_SKILL_STORE` + action='create' + status 走 record;但 record_
        skill_store helper 强制 action 是固定枚举且 status 由 helper 内部填;我们
        直接走通用 record() 写带 status='failed' 的事件,payload 含 error 字段。
        """
        from chariot.audit import AuditHookManager

        await self._audit_hooks.record(
            AuditHookManager.EVENT_SKILL_STORE,
            status="failed",
            payload={
                "skill_id": None,
                "name": input.name,
                "action": "create",
                "source": "propose",
                "proposer": input.proposer or "agent",
                "checkpoint_id": checkpoint_id,
                "error": error,
            },
        )
