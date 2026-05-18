"""Prompt system repo for bundles, versions, and traces."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest
from chariot.agent.config import ConfigError
from chariot.database.models import PromptBundleRow, PromptTraceRow, PromptVersionRow
from chariot.models.prompt import PromptBundleEntry, PromptTraceEntry, PromptVersionEntry
from chariot.prompt.composer import PromptComposer

DEFAULT_BUNDLE_NAME = "default"
DEFAULT_VERSION = "v1"
_MISSING = object()

DEFAULT_BUNDLE_LAYERS: list[dict[str, Any]] = [
    {
        "name": "base_system",
        "source": "ChatRequest.system",
        "content": "You are Chariot, a local agent. Be direct, accurate, and concise.",
    },
    {
        "name": "developer",
        "source": "runtime default",
        "content": "Prefer clear structure, concrete steps, and minimal verbosity.",
    },
    {
        "name": "runtime",
        "source": "AIAgent runtime context",
        "content": "Use the current conversation context and available tools.",
    },
    {"name": "memory", "source": "not implemented yet", "content": None},
    {"name": "skill", "source": "not implemented yet", "content": None},
    {"name": "tool_instruction", "source": "ChatRequest.tools", "content": None},
    {"name": "tool_choice", "source": "ChatRequest.tool_choice", "content": None},
    {"name": "thinking", "source": "ChatRequest.thinking", "content": None},
]


class PromptRepo:
    """Data access for prompt bundles, versions, and traces."""

    DEFAULT_BUNDLE_NAME = DEFAULT_BUNDLE_NAME
    DEFAULT_VERSION = DEFAULT_VERSION

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed_if_empty(self) -> None:
        if await self._bundle_count() > 0:
            return
        bundle = PromptBundleRow(
            name=self.DEFAULT_BUNDLE_NAME,
            description="Default prompt bundle for the current runtime",
            layers=self._serialize_json("layers", DEFAULT_BUNDLE_LAYERS),
            is_active=1,
        )
        self.session.add(bundle)
        await self.session.flush()
        version = PromptVersionRow(
            bundle_id=bundle.id,
            version=self.DEFAULT_VERSION,
            spec=self._serialize_json(
                "spec",
                {
                    "bundle": bundle.name,
                    "version": self.DEFAULT_VERSION,
                    "layers": DEFAULT_BUNDLE_LAYERS,
                },
            ),
            is_active=1,
        )
        self.session.add(version)
        await self.session.commit()

    async def list_bundles(self) -> list[PromptBundleEntry]:
        stmt = select(PromptBundleRow).order_by(PromptBundleRow.updated_at.desc(), PromptBundleRow.name.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            self._bundle_to_entry(
                row,
                version_count=await self._version_count(row.id),
                active_version=await self._active_version_name(row.id),
            )
            for row in rows
        ]

    async def get_bundle(self, ref: str) -> PromptBundleEntry | None:
        row = await self._bundle_row(ref)
        if row is None:
            return None
        return self._bundle_to_entry(
            row,
            version_count=await self._version_count(row.id),
            active_version=await self._active_version_name(row.id),
        )

    async def list_versions(self, bundle_ref: str) -> list[PromptVersionEntry]:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            return []
        stmt = (
            select(PromptVersionRow)
            .where(PromptVersionRow.bundle_id == bundle.id)
            .order_by(PromptVersionRow.created_at.desc(), PromptVersionRow.version.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._version_to_entry(row, bundle_name=bundle.name) for row in rows]

    async def get_version(self, bundle_ref: str, version: str) -> PromptVersionEntry | None:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            return None
        stmt = select(PromptVersionRow).where(
            PromptVersionRow.bundle_id == bundle.id,
            PromptVersionRow.version == version,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._version_to_entry(row, bundle_name=bundle.name)

    async def get_active_bundle(self) -> PromptBundleEntry | None:
        stmt = select(PromptBundleRow).where(PromptBundleRow.is_active == 1).order_by(PromptBundleRow.updated_at.desc())
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._bundle_to_entry(
            row,
            version_count=await self._version_count(row.id),
            active_version=await self._active_version_name(row.id),
        )

    async def get_active_version(self, bundle_name: str | None = None) -> PromptVersionEntry | None:
        bundle = (
            await self._bundle_row_by_name(bundle_name) if bundle_name is not None else await self.get_active_bundle()
        )
        if bundle is None:
            return None
        stmt = select(PromptVersionRow).where(
            PromptVersionRow.bundle_id == bundle.id,
            PromptVersionRow.is_active == 1,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            stmt = (
                select(PromptVersionRow)
                .where(PromptVersionRow.bundle_id == bundle.id)
                .order_by(PromptVersionRow.created_at.desc(), PromptVersionRow.version.desc())
            )
            row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            return None
        return self._version_to_entry(row, bundle_name=bundle.name)

    async def list_traces_by_bundle(
        self,
        bundle_ref: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptTraceEntry]:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            return []
        stmt = (
            select(PromptTraceRow)
            .where(PromptTraceRow.bundle_id == bundle.id)
            .order_by(PromptTraceRow.created_at.desc(), PromptTraceRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [await self._trace_to_entry(row) for row in rows]

    async def list_traces(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptTraceEntry]:
        stmt = (
            select(PromptTraceRow)
            .order_by(PromptTraceRow.created_at.desc(), PromptTraceRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [await self._trace_to_entry(row) for row in rows]

    async def list_trace_versions(self) -> list[PromptVersionEntry]:
        stmt = select(PromptVersionRow).order_by(PromptVersionRow.updated_at.desc(), PromptVersionRow.version.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        bundles = await self._bundle_name_map()
        return [self._version_to_entry(row, bundle_name=bundles.get(row.bundle_id, row.bundle_id)) for row in rows]

    async def create_bundle(
        self,
        ref: str,
        *,
        description: str | None = None,
        layers: list[dict[str, Any]] | None = None,
        version: str = DEFAULT_VERSION,
    ) -> PromptVersionEntry:
        if await self._bundle_row_by_name(ref) is not None:
            raise ConfigError(f"prompt bundle {ref!r} already exists")
        bundle = PromptBundleRow(
            name=ref,
            description=description,
            layers=self._serialize_json("layers", layers or DEFAULT_BUNDLE_LAYERS),
            is_active=1,
        )
        self.session.add(bundle)
        await self.session.flush()
        created = await self._create_version(
            bundle.id,
            bundle.name,
            version,
            layers=layers or DEFAULT_BUNDLE_LAYERS,
            activate=True,
        )
        await self._set_bundle_active(bundle.id)
        await self.session.commit()
        return created

    async def update_bundle(
        self,
        ref: str,
        *,
        description: str | None | object = _MISSING,
        layers: list[dict[str, Any]] | None | object = _MISSING,
        activate: bool = True,
    ) -> PromptVersionEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"prompt bundle {ref!r} not found")
        if description is not _MISSING:
            bundle.description = cast(str | None, description)
        if layers is not _MISSING and layers is not None:
            bundle.layers = self._serialize_json("layers", cast(list[dict[str, Any]], layers))
        await self.session.flush()
        current_layers = cast(list[dict[str, Any]], json.loads(bundle.layers))
        target_layers = cast(list[dict[str, Any]], layers if layers not in (_MISSING, None) else current_layers)

        # 与上一版本比较内容;相同(忽略字段顺序)则不创建新版本
        last_ver = await self._get_latest_version(bundle.id)
        if last_ver is not None:
            last_spec = cast(dict[str, Any], json.loads(last_ver.spec))
            last_layers = cast(list[dict[str, Any]], last_spec.get("layers", []))
            if self._layers_equal(last_layers, target_layers):
                entry = self._version_to_entry(last_ver, bundle_name=bundle.name)
                if activate:
                    await self._set_bundle_active(bundle.id)
                    await self._set_version_active(bundle.id, entry.version)
                    await self.session.commit()
                return entry

        next_version = await self._next_version_name(bundle.id)
        created = await self._create_version(
            bundle.id,
            bundle.name,
            next_version,
            layers=target_layers,
            activate=activate,
        )
        if activate:
            await self._set_bundle_active(bundle.id)
            await self._set_version_active(bundle.id, created.version)
        await self.session.commit()
        return created

    async def rename_bundle(self, ref: str, *, new_name: str) -> PromptBundleEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"prompt bundle {ref!r} not found")
        new_name = new_name.strip()
        if not new_name:
            raise ConfigError("prompt bundle name must be non-empty")
        existing = await self._bundle_row_by_name(new_name)
        if existing is not None and existing.id != bundle.id:
            raise ConfigError(f"prompt bundle {new_name!r} already exists")
        bundle.name = new_name
        versions = (
            (await self.session.execute(select(PromptVersionRow).where(PromptVersionRow.bundle_id == bundle.id)))
            .scalars()
            .all()
        )
        for row in versions:
            spec = cast(dict[str, Any], json.loads(row.spec))
            spec["bundle"] = new_name
            row.spec = self._serialize_json("spec", spec)
        await self.session.commit()
        renamed = await self.get_bundle(new_name)
        if renamed is None:
            raise RuntimeError("prompt bundle rename failed")
        return renamed

    async def activate_bundle(self, ref: str) -> PromptBundleEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"prompt bundle {ref!r} not found")
        await self._set_bundle_active(bundle.id)
        await self.session.commit()
        active = await self.get_bundle(bundle.id)
        if active is None:
            raise RuntimeError("prompt bundle activation failed")
        return active

    async def activate_version(self, bundle_ref: str, version: str) -> PromptVersionEntry:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            raise ConfigError(f"prompt bundle {bundle_ref!r} not found")
        target = await self._set_version_active(bundle.id, version)
        await self._set_bundle_active(bundle.id)
        await self.session.commit()
        return self._version_to_entry(target, bundle_name=bundle.name)

    async def get_trace(self, trace_id: str) -> PromptTraceEntry | None:
        row = await self.session.get(PromptTraceRow, trace_id)
        if row is None:
            return None
        return await self._trace_to_entry(row)

    async def record_trace(
        self,
        req: ChatRequest,
        *,
        provider_id: str | None = None,
        provider_snapshot: str,
        model: str | None,
        bundle_name: str | None = None,
        version: str | None = None,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: dict[str, Any] | None = None,
    ) -> PromptTraceEntry:
        await self.seed_if_empty()
        bundle = (
            await self._bundle_row_by_name(bundle_name) if bundle_name is not None else await self._active_bundle_row()
        )
        if bundle is None:
            bundle = await self._bundle_row_by_name(DEFAULT_BUNDLE_NAME)
        if bundle is None:
            raise RuntimeError("prompt bundle seed failed")
        if version is None:
            ver = await self.get_active_version(bundle.name)
        else:
            ver = await self.get_version(bundle.name, version)
        if ver is None:
            version_name = version or self.DEFAULT_VERSION
            ver = await self._create_version(bundle.id, bundle.name, version_name)
        snapshot = PromptComposer.build_snapshot(
            req,
            provider_id=provider_id,
            provider_snapshot=provider_snapshot,
            model=model,
            bundle_name=bundle.name,
            version=ver.version,
            memory_entries=memory_entries,
            memory_policy=memory_policy,
        )
        row = PromptTraceRow(
            bundle_id=bundle.id,
            version_id=ver.id,
            conversation_id=req.conversation_id,
            provider_id=provider_id,
            provider_snapshot=provider_snapshot,
            model=model,
            request=self._serialize_json("request", snapshot.request),
            source_refs=self._serialize_json("source_refs", snapshot.source_refs),
            prompt_size=snapshot.prompt_size,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return await self._trace_to_entry(row)

    async def _get_latest_version(self, bundle_id: str) -> PromptVersionRow | None:
        """获取 bundle 的最新版本(按 created_at desc,version desc)。"""
        stmt = (
            select(PromptVersionRow)
            .where(PromptVersionRow.bundle_id == bundle_id)
            .order_by(PromptVersionRow.created_at.desc(), PromptVersionRow.version.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _layers_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
        """深度比较两个 layers 列表,忽略 dict 键顺序和列表项顺序。"""
        if len(a) != len(b):
            return False

        def _normalize(val: Any) -> Any:
            if isinstance(val, dict):
                return {k: _normalize(v) for k, v in sorted(val.items()) if v is not None}
            if isinstance(val, list):
                return [_normalize(v) for v in val]
            return val

        a_norm = sorted([_normalize(layer) for layer in a], key=lambda x: json.dumps(x, sort_keys=True))
        b_norm = sorted([_normalize(layer) for layer in b], key=lambda x: json.dumps(x, sort_keys=True))
        return a_norm == b_norm

    async def _create_version(
        self,
        bundle_id: str,
        bundle_name: str,
        version: str,
        *,
        layers: list[dict[str, Any]] | None = None,
        activate: bool = False,
    ) -> PromptVersionEntry:
        row = PromptVersionRow(
            bundle_id=bundle_id,
            version=version,
            spec=self._serialize_json(
                "spec",
                {
                    "bundle": bundle_name,
                    "version": version,
                    "layers": layers or DEFAULT_BUNDLE_LAYERS,
                },
            ),
            is_active=1 if activate else 0,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return self._version_to_entry(row, bundle_name=bundle_name)

    async def _next_version_name(self, bundle_id: str) -> str:
        stmt = select(PromptVersionRow.version).where(PromptVersionRow.bundle_id == bundle_id)
        versions = [row[0] for row in (await self.session.execute(stmt)).all()]
        numbers = [
            int(v[1:]) for v in versions if isinstance(v, str) and len(v) > 1 and v[0] == "v" and v[1:].isdigit()
        ]
        return f"v{(max(numbers) if numbers else 0) + 1}"

    async def _bundle_count(self) -> int:
        stmt = select(func.count(PromptBundleRow.id))
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _version_count(self, bundle_id: str) -> int:
        stmt = select(func.count(PromptVersionRow.id)).where(PromptVersionRow.bundle_id == bundle_id)
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _bundle_row_by_name(self, name: str) -> PromptBundleRow | None:
        stmt = select(PromptBundleRow).where(PromptBundleRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _bundle_row(self, ref: str) -> PromptBundleRow | None:
        stmt = select(PromptBundleRow).where((PromptBundleRow.id == ref) | (PromptBundleRow.name == ref))
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        exact_id = [row for row in rows if row.id == ref]
        if len(exact_id) == 1:
            return exact_id[0]
        exact_name = [row for row in rows if row.name == ref]
        if len(exact_name) == 1:
            return exact_name[0]
        raise ConfigError(f"prompt bundle 引用 {ref!r} 不唯一,请改用 id")

    async def _active_bundle_row(self) -> PromptBundleRow | None:
        stmt = select(PromptBundleRow).where(PromptBundleRow.is_active == 1).order_by(PromptBundleRow.updated_at.desc())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _bundle_name_map(self) -> dict[str, str]:
        stmt = select(PromptBundleRow.id, PromptBundleRow.name)
        rows = (await self.session.execute(stmt)).all()
        return {row.id: row.name for row in rows}

    async def _active_version_name(self, bundle_id: str) -> str | None:
        stmt = select(PromptVersionRow.version).where(
            PromptVersionRow.bundle_id == bundle_id,
            PromptVersionRow.is_active == 1,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return cast(str | None, row)

    async def _set_bundle_active(self, bundle_id: str) -> None:
        stmt = select(PromptBundleRow)
        rows = (await self.session.execute(stmt)).scalars().all()
        for row in rows:
            row.is_active = 1 if row.id == bundle_id else 0

    async def _set_version_active(self, bundle_id: str, version: str) -> PromptVersionRow:
        stmt = select(PromptVersionRow).where(PromptVersionRow.bundle_id == bundle_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        target: PromptVersionRow | None = None
        for row in rows:
            if row.version == version:
                target = row
            row.is_active = 0
        if target is None:
            raise ConfigError(f"prompt version {bundle_id!r}:{version!r} not found")
        target.is_active = 1
        return target

    @staticmethod
    def _bundle_to_entry(
        row: PromptBundleRow,
        *,
        version_count: int = 0,
        active_version: str | None = None,
    ) -> PromptBundleEntry:
        return PromptBundleEntry(
            id=row.id,
            name=row.name,
            description=row.description,
            layers=cast(list[dict[str, Any]], json.loads(row.layers)),
            is_active=bool(row.is_active),
            version_count=version_count,
            active_version=active_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _version_to_entry(self, row: PromptVersionRow, *, bundle_name: str) -> PromptVersionEntry:
        return PromptVersionEntry(
            id=row.id,
            bundle_id=row.bundle_id,
            bundle_name=bundle_name,
            version=row.version,
            spec=cast(dict[str, Any], json.loads(row.spec)),
            is_active=bool(row.is_active),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _trace_to_entry(self, row: PromptTraceRow) -> PromptTraceEntry:
        bundle = await self.session.get(PromptBundleRow, row.bundle_id)
        version = await self.session.get(PromptVersionRow, row.version_id)
        return PromptTraceEntry(
            id=row.id,
            bundle_id=row.bundle_id,
            bundle_name=bundle.name if bundle is not None else row.bundle_id,
            version_id=row.version_id,
            version=version.version if version is not None else row.version_id,
            conversation_id=row.conversation_id,
            provider_id=row.provider_id,
            provider_snapshot=row.provider_snapshot,
            model=row.model,
            request=cast(dict[str, Any], json.loads(row.request)),
            source_refs=cast(list[dict[str, Any]], json.loads(row.source_refs)),
            prompt_size=row.prompt_size,
            created_at=row.created_at,
        )

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"prompt {label} not JSON serializable: {e}") from e
