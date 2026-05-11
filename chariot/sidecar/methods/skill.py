"""sidecar skill RPC adapters(B6 wave 1)。

读路径(`list_skills` / `get_skill`)走 `SkillRegistry`(builtin + DB union 视图);
写路径(`install_skill` / `enable_skill` / `disable_skill` / `delete_skill`)走
`SkillRepo` + `audit_hooks.record_skill_store`。

注意:install_skill **不**热重载 registry —— 跟 CLI 同语义,需要 sidecar 进程
重启才反映到 chat path(避免运行时多个 chat 看到不一致的 skill 集合)。
"""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ConfigError
from chariot.repos.skill_repo import SkillEntry, SkillRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.skills import BaseSkill, SkillLoader, SkillManifestError


class SkillMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx, params
        registry = self.agent.skill_registry
        if registry is None:
            return {"skills": []}
        return {"skills": [self._skill_to_dict(s) for s in registry.list_all()]}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        registry = self.agent.skill_registry
        skill = registry.get(name) if registry is not None else None
        if skill is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"skill {name!r} not found")
        return {"skill": self._skill_to_dict(skill, full=True)}

    async def install(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """传 `content`(YAML 文本)或 `from_builtin`(builtin name)两种入参之一。

        - `content`:任意 YAML,解析后落 DB
        - `from_builtin`:从 registry 拿 builtin manifest,转 YAML 写 DB(fork)
        """
        del ctx
        enabled = bool(params.get("enabled", True))
        content = self._optional_str(params, "content")
        from_builtin = self._optional_str(params, "from_builtin")
        if content is None and from_builtin is None:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "需要 `content` 或 `from_builtin` 之一",
            )
        if content is not None and from_builtin is not None:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "`content` 与 `from_builtin` 不可同时传",
            )

        yaml_text: str
        skill_name: str
        if from_builtin is not None:
            registry = self.agent.skill_registry
            skill = registry.get(from_builtin) if registry is not None else None
            if skill is None or skill.source != "builtin":
                raise RpcError(
                    JsonRpcServer.ERR_NOT_FOUND,
                    f"no builtin skill named {from_builtin!r}",
                )
            yaml_text = _manifest_to_yaml(skill.manifest)
            skill_name = skill.name
        else:
            try:
                manifest = SkillLoader.parse_yaml(content or "")
            except SkillManifestError as exc:
                raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(exc)) from exc
            yaml_text = content or ""
            skill_name = manifest.name

        async with self._session() as session:
            try:
                entry = await SkillRepo(session).create(
                    name=skill_name,
                    description=None,
                    content=yaml_text,
                    enabled=enabled,
                )
            except ConfigError as exc:
                raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(exc)) from exc
        await self.agent.audit_hooks.record_skill_store(
            skill_id=entry.id,
            name=entry.name,
            action="create",
        )
        return {"skill": self._entry_to_dict(entry)}

    async def enable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        return await self._set_enabled(params, ctx, enabled=True)

    async def disable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        return await self._set_enabled(params, ctx, enabled=False)

    async def curate(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """B6 wave 4:跑 SkillCurator;返 4 bucket(stale/underused/failing/overlapping)。

        无 registry / 无 sessionmaker → 退化空结果(让前端简单展示"暂无数据")。
        """
        del ctx, params
        registry = self.agent.skill_registry
        if registry is None:
            return {"stale": [], "underused": [], "failing": [], "overlapping": []}
        from chariot.skills import SkillCurator

        curator = SkillCurator(sessionmaker=self.agent.session_maker, skill_registry=registry)
        result = await curator.curate()
        return {
            "stale": list(result.stale),
            "underused": list(result.underused),
            "failing": list(result.failing),
            "overlapping": [{"a": a, "b": b, "ratio": ratio} for a, b, ratio in result.overlapping],
        }

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            repo = SkillRepo(session)
            entry = await repo.get_by_name(name)
            if entry is None:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"no DB skill named {name!r}")
            await repo.delete(entry.id)
        await self.agent.audit_hooks.record_skill_store(
            skill_id=entry.id,
            name=entry.name,
            action="delete",
        )
        return {"deleted": entry.name}

    # ---- 内部 ----

    async def _set_enabled(
        self,
        params: dict[str, Any],
        ctx: RpcContext,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            repo = SkillRepo(session)
            entry = await repo.get_by_name(name)
            if entry is None:
                raise RpcError(
                    JsonRpcServer.ERR_NOT_FOUND,
                    f"no DB skill named {name!r}(builtin 永远 enabled)",
                )
            updated = await repo.set_enabled(entry.id, enabled)
        await self.agent.audit_hooks.record_skill_store(
            skill_id=updated.id,
            name=updated.name,
            action="enable" if enabled else "disable",
        )
        return {"skill": self._entry_to_dict(updated)}

    @staticmethod
    def _skill_to_dict(skill: BaseSkill, *, full: bool = False) -> dict[str, Any]:
        m = skill.manifest
        out: dict[str, Any] = {
            "name": m.name,
            "source": skill.source,
            "version": m.version,
            "enabled": skill.enabled,
            "description": m.description,
            "tags": list(m.tags),
        }
        if full:
            out["prompt"] = m.prompt
            out["allowed_tools"] = list(m.allowed_tools) if m.allowed_tools is not None else None
            out["forbidden_tools"] = list(m.forbidden_tools)
        return out

    @staticmethod
    def _entry_to_dict(entry: SkillEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "enabled": entry.enabled,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }


def _manifest_to_yaml(manifest) -> str:  # type: ignore[no-untyped-def]
    """跟 CLI 同款 manifest → YAML 文本(避免 dump 依赖,手拼;字段顺序对齐
    builtin 模板)。"""
    lines = [
        f"schema_version: {manifest.schema_version}",
        f"name: {manifest.name}",
        f'version: "{manifest.version}"',
        f"description: {manifest.description}",
        "prompt: |",
    ]
    for line in manifest.prompt.splitlines():
        lines.append(f"  {line}")
    if manifest.allowed_tools is not None:
        lines.append("allowed_tools:")
        for t in manifest.allowed_tools:
            lines.append(f"  - {t}")
    if manifest.forbidden_tools:
        lines.append("forbidden_tools:")
        for t in manifest.forbidden_tools:
            lines.append(f"  - {t}")
    if manifest.tags:
        lines.append("tags:")
        for t in manifest.tags:
            lines.append(f"  - {t}")
    return "\n".join(lines) + "\n"
