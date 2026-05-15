"""`chariot skill <subcmd>` —— B6 wave 1 全套(builtin + DB union 视图 + CRUD)。

子命令:
- `chariot skill list`:列 registry 全部 skill(builtin + DB,union 视图)
- `chariot skill show <name>`:看 manifest(prompt / allowed_tools / source / version)
- `chariot skill install --from <path-or-name>`:从 YAML 文件 / builtin name 装到 DB
  (让用户能 patch builtin;builtin 永远 in-place,DB 行覆盖)
- `chariot skill enable <name>` / `disable <name>`:开/关 DB skill(builtin 不能 disable)
- `chariot skill remove <name>`:删 DB skill 行(不影响 builtin)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chariot.agent.config import ConfigError
from chariot.audit.hooks import AuditHookManager
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.skill_repo import SkillEntry
from chariot.services.audit import AuditService
from chariot.services.skill import SkillService
from chariot.skills import SkillLoader, SkillManifestError, SkillRegistry

skill_app = typer.Typer(
    name="skill",
    help="Skill 注册表(builtin YAML + DB 行 union)+ CRUD",
    no_args_is_help=True,
)


@skill_app.command("list", help="列 registry 全部 skill(union 视图)")
def list_cmd() -> None:
    asyncio.run(_list())


@skill_app.command("show", help="查看 manifest 详情")
def show_cmd(
    name: Annotated[str, typer.Argument(help="skill name")],
) -> None:
    asyncio.run(_show(name))


@skill_app.command("install", help="从 YAML 文件 / builtin name 装到 DB")
def install_cmd(
    from_: Annotated[
        str,
        typer.Option("--from", help="YAML 文件路径 或 builtin skill name(将其 fork 到 DB)"),
    ],
    enabled: Annotated[
        bool,
        typer.Option("--enabled/--disabled", help="装好后是否立即 enable;默认 enabled"),
    ] = True,
) -> None:
    asyncio.run(_install(from_, enabled))


@skill_app.command("enable", help="enable DB skill(builtin 永远 on,不需要 enable)")
def enable_cmd(
    name: Annotated[str, typer.Argument(help="skill name")],
) -> None:
    asyncio.run(_set_enabled(name, True))


@skill_app.command("disable", help="disable DB skill")
def disable_cmd(
    name: Annotated[str, typer.Argument(help="skill name")],
) -> None:
    asyncio.run(_set_enabled(name, False))


@skill_app.command("remove", help="删 DB skill 行(builtin 不可删)")
def remove_cmd(
    name: Annotated[str, typer.Argument(help="skill name")],
) -> None:
    asyncio.run(_remove(name))


@skill_app.command("proposals", help="列历史 propose_skill 调用记录(read-only audit 视图)")
def proposals_cmd(
    limit: Annotated[int, typer.Option("--limit", help="返回条数上限")] = 20,
) -> None:
    asyncio.run(_proposals(limit))


@skill_app.command("curate", help="跑 4-bucket 静态分析(stale / underused / failing / overlapping)")
def curate_cmd() -> None:
    asyncio.run(_curate())


# ---- 实现 ----


async def _list() -> None:
    async with installed_runtime() as agent:
        registry = agent.skill_registry
        if registry is None:
            Renderer.out("(skill registry not loaded)")
            return
        skills = registry.list_all()
    if not skills:
        Renderer.out("(没有 skills)")
        return
    rows = [
        (
            s.name,
            s.source,
            s.manifest.version,
            "yes" if s.enabled else "no",
            ",".join(s.manifest.tags) if s.manifest.tags else "-",
            _truncate(s.manifest.description, 60),
        )
        for s in skills
    ]
    Renderer.table(
        ["name", "source", "version", "enabled", "tags", "description"],
        rows,
        title="skills",
    )


async def _show(name: str) -> None:
    async with installed_runtime() as agent:
        registry = agent.skill_registry
        if registry is None:
            Renderer.die("skill registry not loaded")
            return
        skill = registry.get(name)
    if skill is None:
        Renderer.die(f"skill not found: {name!r}")
        return
    m = skill.manifest
    Renderer.kv(
        {
            "name": m.name,
            "version": m.version,
            "source": skill.source,
            "enabled": "yes" if skill.enabled else "no",
            "tags": ",".join(m.tags) if m.tags else "-",
            "allowed_tools": ",".join(m.allowed_tools) if m.allowed_tools else "(unrestricted)",
            "forbidden_tools": ",".join(m.forbidden_tools) if m.forbidden_tools else "(none)",
        }
    )
    Renderer.out("")
    Renderer.out("description:")
    Renderer.out(m.description)
    Renderer.out("")
    Renderer.out("prompt:")
    Renderer.out(m.prompt)


async def _install(from_: str, enabled: bool) -> None:
    source = from_.strip()
    if not source:
        Renderer.die("--from is required")
        return
    yaml_text: str
    skill_name: str
    # 路径形态优先(包含 / \ . 或文件后缀);否则当 builtin name fork
    if any(ch in source for ch in ("/", "\\")) or source.endswith((".yaml", ".yml")):
        path = Path(source).expanduser()
        if not path.exists():
            Renderer.die(f"file not found: {path}")
            return
        yaml_text = path.read_text(encoding="utf-8")
        try:
            manifest = SkillLoader.parse_yaml(yaml_text)
        except SkillManifestError as exc:
            Renderer.die(f"invalid YAML: {exc}")
            return
        skill_name = manifest.name
    else:
        # builtin name fork:从 registry 拿 builtin → 转 YAML 重新落 DB
        async with installed_runtime() as agent:
            registry = agent.skill_registry
            if registry is None:
                Renderer.die("skill registry not loaded")
                return
            skill = registry.get(source)
            if skill is None or skill.source != "builtin":
                Renderer.die(f"no builtin skill named {source!r}")
                return
            yaml_text = _manifest_to_yaml(skill.manifest)
            skill_name = skill.name
        async with installed_runtime() as agent:
            try:
                entry = await SkillService(agent).create(
                    name=skill_name,
                    description=_extract_description(yaml_text) or None,
                    content=yaml_text,
                    enabled=enabled,
                )
            except ConfigError as exc:
                Renderer.die(str(exc))
                return
            await agent.audit_hooks.record_skill_store(
                skill_id=entry.id,
                name=entry.name,
                action="create",
            )
    Renderer.out(f"+ {entry.id} {entry.name}{' (disabled)' if not entry.enabled else ''}")


async def _set_enabled(name: str, enabled: bool) -> None:
    async with installed_runtime() as agent:
        entry = await _find_db_entry(agent, name)
        if entry is None:
            Renderer.die(f"no DB skill named {name!r}(builtin 永远 enabled,无需 enable/disable)")
            return
        try:
            updated = await SkillService(agent).set_enabled(entry.id, enabled)
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
        await agent.audit_hooks.record_skill_store(
            skill_id=updated.id,
            name=updated.name,
            action="enable" if enabled else "disable",
        )
    Renderer.out(f"* {updated.name}: {'enabled' if updated.enabled else 'disabled'}")


async def _proposals(limit: int) -> None:
    """筛 audit_events.event_type='skill_store' AND payload.source='propose' 倒序展示。

    展示字段:created_at / status(create=成功 / failed=失败)/ name / checkpoint_id /
    proposer / error(若有)。
    """
    if limit <= 0:
        limit = 20
    async with installed_runtime() as agent:
        # 多取一些(allowance ×4),再 client-side filter source='propose'
        events = await AuditService(agent).list_events(limit=max(limit * 4, 50))
    filtered = [
        ev
        for ev in events
        if ev.event_type == AuditHookManager.EVENT_SKILL_STORE and ev.payload.get("source") == "propose"
    ][:limit]
    if not filtered:
        Renderer.out("(没有 propose 记录)")
        return
    rows = [
        (
            ev.created_at.isoformat(timespec="seconds"),
            ev.status or "-",
            str(ev.payload.get("name") or "-"),
            str(ev.payload.get("checkpoint_id") or "-"),
            str(ev.payload.get("proposer") or "-"),
            _truncate(str(ev.payload.get("error") or ""), 40),
        )
        for ev in filtered
    ]
    Renderer.table(
        ["created_at", "status", "name", "checkpoint", "proposer", "error"],
        rows,
        title="skill proposals",
    )


async def _curate() -> None:
    """读 audit_events.skill_activate + skills.prompt → 4 bucket 分类输出。"""
    from chariot.skills import SkillCurator

    async with installed_runtime() as agent:
        registry = agent.skill_registry
        if registry is None:
            Renderer.die("skill registry not loaded")
            return
        curator = SkillCurator(sessionmaker=agent.session_maker, skill_registry=registry)
        result = await curator.curate()
    Renderer.out(f"stale ({len(result.stale)}):")
    Renderer.out(", ".join(result.stale) or "  (空)")
    Renderer.out("")
    Renderer.out(f"underused ({len(result.underused)}):")
    Renderer.out(", ".join(result.underused) or "  (空)")
    Renderer.out("")
    Renderer.out(f"failing ({len(result.failing)}):")
    Renderer.out(", ".join(result.failing) or "  (空)")
    Renderer.out("")
    Renderer.out(f"overlapping ({len(result.overlapping)}):")
    if result.overlapping:
        for a, b, ratio in result.overlapping:
            Renderer.out(f"  {a} ↔ {b}  (ratio={ratio})")
    else:
        Renderer.out("  (空)")


async def _remove(name: str) -> None:
    async with installed_runtime() as agent:
        entry = await _find_db_entry(agent, name)
        if entry is None:
            Renderer.die(f"no DB skill named {name!r}(builtin 不可删,可改 YAML 文件)")
            return
        ok = await SkillService(agent).delete(entry.id)
        if not ok:
            Renderer.die(f"skill {name!r} disappeared during delete")
            return
        await agent.audit_hooks.record_skill_store(
            skill_id=entry.id,
            name=entry.name,
            action="delete",
        )
    Renderer.out(f"- {entry.name}")


# ---- helpers ----


async def _find_db_entry(runtime, name: str) -> SkillEntry | None:  # type: ignore[no-untyped-def]
    return await SkillService(runtime).get_by_name(name)


def _extract_description(yaml_text: str) -> str | None:
    """从 YAML 文本里解析 description 字段(给 SkillRepo.create 的 description
    列存 metadata)。解析失败 → None。"""
    try:
        manifest = SkillLoader.parse_yaml(yaml_text)
    except SkillManifestError:
        return None
    return manifest.description


def _manifest_to_yaml(manifest) -> str:  # type: ignore[no-untyped-def]
    """builtin fork 时把 manifest 转回 YAML 文本(避免引 yaml.dump 依赖,手拼)。
    保持字段顺序跟 builtin YAML 模板一致。"""
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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# 防 unused import 提示;tests 会 import SkillRegistry 单独
_ = SkillRegistry


def register(app: typer.Typer) -> None:
    app.add_typer(skill_app)
