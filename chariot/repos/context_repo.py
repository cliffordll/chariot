"""ContextRepo: context bundles, versions, snapshots, and traces."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import ContextBundleRow, ContextSnapshotRow, ContextTraceRow, ContextVersionRow
from chariot.models.context import (
    ContextBundleEntry,
    ContextSnapshot,
    ContextSnapshotEntry,
    ContextTraceEntry,
    ContextVersionEntry,
)

DEFAULT_BUNDLE_NAME = "default"
DEFAULT_VERSION = "v1"
_MISSING = object()
DEFAULT_CONTEXT_SPEC: dict[str, Any] = {
    "version": "v1",
    "name": "default_context_policy",
    "include_conversation_history": True,
    "include_runtime_state": True,
    "include_memory_state": True,
    "include_tool_state": True,
    "include_skill_state": True,
    "include_provider_state": True,
    "include_policy_state": True,
    "trimmed": False,
}


class ContextRepo:
    """`context_snapshots` / `context_traces` table data access."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed_if_empty(self) -> None:
        if await self._bundle_count() > 0:
            return
        bundle = ContextBundleRow(
            name=DEFAULT_BUNDLE_NAME,
            description="Default context bundle for the current runtime",
            is_active=1,
        )
        self.session.add(bundle)
        await self.session.flush()
        version = ContextVersionRow(
            bundle_id=bundle.id,
            version=DEFAULT_VERSION,
            spec=self._serialize_json("spec", DEFAULT_CONTEXT_SPEC),
            is_active=1,
        )
        self.session.add(version)
        await self.session.commit()

    async def list_bundles(self) -> list[ContextBundleEntry]:
        stmt = select(ContextBundleRow).order_by(ContextBundleRow.updated_at.desc(), ContextBundleRow.name.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            self._bundle_to_entry(
                row,
                version_count=await self._version_count(row.id),
                active_version=await self._active_version_name(row.id),
            )
            for row in rows
        ]

    async def get_bundle(self, ref: str) -> ContextBundleEntry | None:
        row = await self._bundle_row(ref)
        if row is None:
            return None
        return self._bundle_to_entry(
            row,
            version_count=await self._version_count(row.id),
            active_version=await self._active_version_name(row.id),
        )

    async def get_active_bundle(self) -> ContextBundleEntry | None:
        row = await self._active_bundle_row()
        if row is None:
            return None
        return self._bundle_to_entry(
            row,
            version_count=await self._version_count(row.id),
            active_version=await self._active_version_name(row.id),
        )

    async def get_active_version(self, bundle_name: str | None = None) -> ContextVersionEntry | None:
        bundle = (
            await self._bundle_row_by_name(bundle_name) if bundle_name is not None else await self._active_bundle_row()
        )
        if bundle is None:
            return None
        stmt = select(ContextVersionRow).where(
            ContextVersionRow.bundle_id == bundle.id,
            ContextVersionRow.is_active == 1,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            stmt = (
                select(ContextVersionRow)
                .where(ContextVersionRow.bundle_id == bundle.id)
                .order_by(ContextVersionRow.created_at.desc(), ContextVersionRow.version.desc())
            )
            row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            return None
        return self._version_to_entry(row, bundle_name=bundle.name)

    async def list_versions(self, bundle_ref: str) -> list[ContextVersionEntry]:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            return []
        stmt = (
            select(ContextVersionRow)
            .where(ContextVersionRow.bundle_id == bundle.id)
            .order_by(ContextVersionRow.created_at.desc(), ContextVersionRow.version.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._version_to_entry(row, bundle_name=bundle.name) for row in rows]

    async def get_version(self, bundle_ref: str, version: str) -> ContextVersionEntry | None:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            return None
        stmt = select(ContextVersionRow).where(
            ContextVersionRow.bundle_id == bundle.id,
            ContextVersionRow.version == version,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._version_to_entry(row, bundle_name=bundle.name)

    async def create_bundle(
        self,
        ref: str,
        *,
        description: str | None = None,
        spec: dict[str, Any] | None = None,
        version: str = DEFAULT_VERSION,
    ) -> ContextVersionEntry:
        if await self._bundle_row_by_name(ref) is not None:
            raise ConfigError(f"context bundle {ref!r} already exists")
        bundle = ContextBundleRow(name=ref, description=description, is_active=1)
        self.session.add(bundle)
        await self.session.flush()
        created = await self._create_version(
            bundle.id,
            bundle.name,
            version,
            spec=spec or DEFAULT_CONTEXT_SPEC,
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
        spec: dict[str, Any] | None | object = _MISSING,
        activate: bool = True,
    ) -> ContextVersionEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"context bundle {ref!r} not found")
        description_missing = description is _MISSING
        spec_missing = spec is _MISSING
        if not description_missing:
            bundle.description = cast(str | None, description)
        target_spec = DEFAULT_CONTEXT_SPEC
        if spec_missing:
            last_ver = await self._get_latest_version(bundle.id)
            if last_ver is not None:
                target_spec = cast(dict[str, Any], json.loads(last_ver.spec))
        else:
            target_spec = cast(dict[str, Any], spec if spec is not None else DEFAULT_CONTEXT_SPEC)
        last_ver = await self._get_latest_version(bundle.id)
        if last_ver is not None:
            last_spec = cast(dict[str, Any], json.loads(last_ver.spec))
            if last_spec == target_spec:
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
            spec=target_spec,
            activate=activate,
        )
        if activate:
            await self._set_bundle_active(bundle.id)
            await self._set_version_active(bundle.id, created.version)
        await self.session.commit()
        return created

    async def rename_bundle(self, ref: str, *, new_name: str) -> ContextBundleEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"context bundle {ref!r} not found")
        new_name = new_name.strip()
        if not new_name:
            raise ConfigError("context bundle name must be non-empty")
        existing = await self._bundle_row_by_name(new_name)
        if existing is not None and existing.id != bundle.id:
            raise ConfigError(f"context bundle {new_name!r} already exists")
        bundle.name = new_name
        versions = (
            (await self.session.execute(select(ContextVersionRow).where(ContextVersionRow.bundle_id == bundle.id)))
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
            raise RuntimeError("context bundle rename failed")
        return renamed

    async def activate_bundle(self, ref: str) -> ContextBundleEntry:
        bundle = await self._bundle_row(ref)
        if bundle is None:
            raise ConfigError(f"context bundle {ref!r} not found")
        await self._set_bundle_active(bundle.id)
        await self.session.commit()
        active = await self.get_bundle(bundle.id)
        if active is None:
            raise RuntimeError("context bundle activation failed")
        return active

    async def activate_version(self, bundle_ref: str, version: str) -> ContextVersionEntry:
        bundle = await self._bundle_row(bundle_ref)
        if bundle is None:
            raise ConfigError(f"context bundle {bundle_ref!r} not found")
        target = await self._set_version_active(bundle.id, version)
        await self._set_bundle_active(bundle.id)
        await self.session.commit()
        return self._version_to_entry(target, bundle_name=bundle.name)

    async def list_snapshots(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextSnapshotEntry]:
        stmt = select(ContextSnapshotRow).order_by(
            ContextSnapshotRow.created_at.desc(),
            ContextSnapshotRow.id.desc(),
        )
        if conversation_id is not None:
            stmt = stmt.where(ContextSnapshotRow.conversation_id == conversation_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._snapshot_to_entry(row) for row in rows]

    async def get_snapshot(self, snapshot_id: str) -> ContextSnapshotEntry | None:
        row = await self.session.get(ContextSnapshotRow, snapshot_id)
        return self._snapshot_to_entry(row) if row is not None else None

    async def list_traces(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextTraceEntry]:
        stmt = select(ContextTraceRow).order_by(
            ContextTraceRow.created_at.desc(),
            ContextTraceRow.id.desc(),
        )
        if conversation_id is not None:
            stmt = stmt.where(ContextTraceRow.conversation_id == conversation_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [await self._trace_to_entry_async(row) for row in rows]

    async def get_trace(self, trace_id: str) -> ContextTraceEntry | None:
        row = await self.session.get(ContextTraceRow, trace_id)
        return await self._trace_to_entry_async(row) if row is not None else None

    async def get_trace_by_snapshot_id(self, snapshot_id: str) -> ContextTraceEntry | None:
        stmt = select(ContextTraceRow).where(ContextTraceRow.snapshot_id == snapshot_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return await self._trace_to_entry_async(row) if row is not None else None

    async def record_snapshot(self, snapshot: ContextSnapshot) -> ContextSnapshotEntry:
        row = ContextSnapshotRow(
            conversation_id=snapshot.conversation_id,
            provider_id=snapshot.provider_id,
            provider_snapshot=snapshot.provider_snapshot,
            model=snapshot.model,
            request=self._serialize_json("request", snapshot.request),
            slices=self._serialize_json("slices", [self._slice_to_dict(s) for s in snapshot.slices]),
            source_refs=self._serialize_json("source_refs", snapshot.source_refs),
            context_size=snapshot.context_size,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._snapshot_to_entry(row)

    async def record_trace(
        self,
        snapshot_id: str,
        *,
        prompt_trace_id: str | None = None,
        policy_name: str = "default_context_policy",
        bundle_id: str | None = None,
        version_id: str | None = None,
    ) -> ContextTraceEntry:
        snapshot = await self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ConfigError(f"context snapshot {snapshot_id!r} not found")
        selected_refs = [
            {
                **ref,
                "included": bool(ref.get("present", False)),
                "reason": "available" if ref.get("present", False) else "missing",
            }
            for ref in snapshot.source_refs
        ]
        row = ContextTraceRow(
            snapshot_id=snapshot_id,
            bundle_id=bundle_id,
            version_id=version_id,
            conversation_id=snapshot.conversation_id,
            provider_id=snapshot.provider_id,
            provider_snapshot=snapshot.provider_snapshot,
            model=snapshot.model,
            prompt_trace_id=prompt_trace_id,
            policy=self._serialize_json(
                "policy",
                {
                    "name": policy_name,
                    "selected_count": len(selected_refs),
                    "excluded_count": sum(1 for ref in selected_refs if not ref.get("included", False)),
                },
            ),
            selected_refs=self._serialize_json("selected_refs", selected_refs),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return await self._trace_to_entry_async(row)

    async def inspect_context(self, context_id: str) -> dict[str, Any] | None:
        snapshot = await self.get_snapshot(context_id)
        trace: ContextTraceEntry | None
        if snapshot is not None:
            trace = await self.get_trace_by_snapshot_id(snapshot.id)
            return {
                "snapshot": self._snapshot_to_dict(snapshot),
                "trace": self._trace_to_dict(trace) if trace is not None else None,
            }
        trace = await self.get_trace(context_id)
        if trace is None:
            return None
        snapshot = await self.get_snapshot(trace.snapshot_id)
        return {
            "snapshot": self._snapshot_to_dict(snapshot) if snapshot is not None else None,
            "trace": self._trace_to_dict(trace),
        }

    @staticmethod
    def _slice_to_dict(slice_: Any) -> dict[str, Any]:
        return {
            "name": slice_.name,
            "source": slice_.source,
            "content": slice_.content,
        }

    @staticmethod
    def _snapshot_to_dict(entry: ContextSnapshotEntry | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "id": entry.id,
            "conversation_id": entry.conversation_id,
            "provider_id": entry.provider_id,
            "provider_snapshot": entry.provider_snapshot,
            "model": entry.model,
            "request": entry.request,
            "slices": entry.slices,
            "source_refs": entry.source_refs,
            "context_size": entry.context_size,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _trace_to_dict(entry: ContextTraceEntry | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "id": entry.id,
            "snapshot_id": entry.snapshot_id,
            "bundle_id": entry.bundle_id,
            "bundle_name": entry.bundle_name,
            "version_id": entry.version_id,
            "version": entry.version,
            "conversation_id": entry.conversation_id,
            "provider_id": entry.provider_id,
            "provider_snapshot": entry.provider_snapshot,
            "model": entry.model,
            "prompt_trace_id": entry.prompt_trace_id,
            "policy": entry.policy,
            "selected_refs": entry.selected_refs,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _snapshot_to_entry(row: ContextSnapshotRow) -> ContextSnapshotEntry:
        return ContextSnapshotEntry(
            id=row.id,
            conversation_id=row.conversation_id,
            provider_id=row.provider_id,
            provider_snapshot=row.provider_snapshot,
            model=row.model,
            request=cast(dict[str, Any], json.loads(row.request)),
            slices=cast(list[dict[str, Any]], json.loads(row.slices)),
            source_refs=cast(list[dict[str, Any]], json.loads(row.source_refs)),
            context_size=row.context_size,
            created_at=row.created_at,
        )

    @staticmethod
    def _trace_to_entry(row: ContextTraceRow) -> ContextTraceEntry:
        bundle_name: str | None = None
        version_name: str | None = None
        if row.bundle_id is not None:
            bundle_name = row.bundle_id
        if row.version_id is not None:
            version_name = row.version_id
        return ContextTraceEntry(
            id=row.id,
            snapshot_id=row.snapshot_id,
            bundle_id=row.bundle_id,
            bundle_name=bundle_name,
            version_id=row.version_id,
            version=version_name,
            conversation_id=row.conversation_id,
            provider_id=row.provider_id,
            provider_snapshot=row.provider_snapshot,
            model=row.model,
            prompt_trace_id=row.prompt_trace_id,
            policy=cast(dict[str, Any], json.loads(row.policy)),
            selected_refs=cast(list[dict[str, Any]], json.loads(row.selected_refs)),
            created_at=row.created_at,
        )

    async def _trace_to_entry_async(self, row: ContextTraceRow) -> ContextTraceEntry:
        bundle_name = None
        version_name = None
        if row.bundle_id is not None:
            bundle = await self.session.get(ContextBundleRow, row.bundle_id)
            bundle_name = bundle.name if bundle is not None else row.bundle_id
        if row.version_id is not None:
            version = await self.session.get(ContextVersionRow, row.version_id)
            version_name = version.version if version is not None else row.version_id
        return ContextTraceEntry(
            id=row.id,
            snapshot_id=row.snapshot_id,
            bundle_id=row.bundle_id,
            bundle_name=bundle_name,
            version_id=row.version_id,
            version=version_name,
            conversation_id=row.conversation_id,
            provider_id=row.provider_id,
            provider_snapshot=row.provider_snapshot,
            model=row.model,
            prompt_trace_id=row.prompt_trace_id,
            policy=cast(dict[str, Any], json.loads(row.policy)),
            selected_refs=cast(list[dict[str, Any]], json.loads(row.selected_refs)),
            created_at=row.created_at,
        )

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"context {label} not JSON serializable: {e}") from e

    async def _bundle_count(self) -> int:
        stmt = select(func.count(ContextBundleRow.id))
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _version_count(self, bundle_id: str) -> int:
        stmt = select(func.count(ContextVersionRow.id)).where(ContextVersionRow.bundle_id == bundle_id)
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _bundle_row_by_name(self, name: str) -> ContextBundleRow | None:
        stmt = select(ContextBundleRow).where(ContextBundleRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _bundle_row(self, ref: str) -> ContextBundleRow | None:
        stmt = select(ContextBundleRow).where((ContextBundleRow.id == ref) | (ContextBundleRow.name == ref))
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
        raise ConfigError(f"context bundle 引用 {ref!r} 不唯一,请改用 id")

    async def _active_bundle_row(self) -> ContextBundleRow | None:
        stmt = (
            select(ContextBundleRow).where(ContextBundleRow.is_active == 1).order_by(ContextBundleRow.updated_at.desc())
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _active_version_name(self, bundle_id: str) -> str | None:
        stmt = select(ContextVersionRow.version).where(
            ContextVersionRow.bundle_id == bundle_id,
            ContextVersionRow.is_active == 1,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return cast(str | None, row)

    async def _set_bundle_active(self, bundle_id: str) -> None:
        stmt = select(ContextBundleRow)
        rows = (await self.session.execute(stmt)).scalars().all()
        for row in rows:
            row.is_active = 1 if row.id == bundle_id else 0

    async def _set_version_active(self, bundle_id: str, version: str) -> ContextVersionRow:
        stmt = select(ContextVersionRow).where(ContextVersionRow.bundle_id == bundle_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        target: ContextVersionRow | None = None
        for row in rows:
            if row.version == version:
                target = row
            row.is_active = 0
        if target is None:
            raise ConfigError(f"context version {bundle_id!r}:{version!r} not found")
        target.is_active = 1
        return target

    async def _get_latest_version(self, bundle_id: str) -> ContextVersionRow | None:
        stmt = (
            select(ContextVersionRow)
            .where(ContextVersionRow.bundle_id == bundle_id)
            .order_by(ContextVersionRow.created_at.desc(), ContextVersionRow.version.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _next_version_name(self, bundle_id: str) -> str:
        stmt = select(ContextVersionRow.version).where(ContextVersionRow.bundle_id == bundle_id)
        versions = [row[0] for row in (await self.session.execute(stmt)).all()]
        numbers = [
            int(v[1:]) for v in versions if isinstance(v, str) and len(v) > 1 and v[0] == "v" and v[1:].isdigit()
        ]
        return f"v{(max(numbers) if numbers else 0) + 1}"

    async def _create_version(
        self,
        bundle_id: str,
        bundle_name: str,
        version: str,
        *,
        spec: dict[str, Any],
        activate: bool = False,
    ) -> ContextVersionEntry:
        row = ContextVersionRow(
            bundle_id=bundle_id,
            version=version,
            spec=self._serialize_json("spec", {**spec, "bundle": bundle_name, "version": version}),
            is_active=1 if activate else 0,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return self._version_to_entry(row, bundle_name=bundle_name)

    @staticmethod
    def _bundle_to_entry(
        row: ContextBundleRow,
        *,
        version_count: int = 0,
        active_version: str | None = None,
    ) -> ContextBundleEntry:
        return ContextBundleEntry(
            id=row.id,
            name=row.name,
            description=row.description,
            is_active=bool(row.is_active),
            version_count=version_count,
            active_version=active_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _version_to_entry(self, row: ContextVersionRow, *, bundle_name: str) -> ContextVersionEntry:
        return ContextVersionEntry(
            id=row.id,
            bundle_id=row.bundle_id,
            bundle_name=bundle_name,
            version=row.version,
            spec=cast(dict[str, Any], json.loads(row.spec)),
            is_active=bool(row.is_active),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
