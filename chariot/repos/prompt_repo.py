"""Prompt system repo for bundles, versions, and traces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.agent.chat_request import ChatRequest
from chariot.database.models import PromptBundleRow, PromptTraceRow, PromptVersionRow
from chariot.prompt.composer import build_snapshot


DEFAULT_BUNDLE_NAME = "default"
DEFAULT_VERSION = "v1"

DEFAULT_BUNDLE_LAYERS: list[dict[str, Any]] = [
    {"name": "base_system", "source": "ChatRequest.system"},
    {"name": "developer", "source": "runtime default"},
    {"name": "runtime", "source": "AIAgent runtime context"},
    {"name": "memory", "source": "not implemented yet"},
    {"name": "skill", "source": "not implemented yet"},
    {"name": "tool_instruction", "source": "ChatRequest.tools"},
    {"name": "tool_choice", "source": "ChatRequest.tool_choice"},
    {"name": "thinking", "source": "ChatRequest.thinking"},
]


@dataclass(frozen=True)
class PromptBundleEntry:
    id: str
    name: str
    description: str | None
    layers: list[dict[str, Any]]
    version_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PromptVersionEntry:
    id: str
    bundle_id: str
    bundle_name: str
    version: str
    spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PromptTraceEntry:
    id: str
    bundle_id: str
    bundle_name: str
    version_id: str
    version: str
    conversation_id: str | None
    provider_name: str
    model: str | None
    request: dict[str, Any]
    source_refs: list[dict[str, Any]]
    prompt_size: int
    created_at: datetime


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
        )
        self.session.add(version)
        await self.session.commit()

    async def list_bundles(self) -> list[PromptBundleEntry]:
        stmt = select(PromptBundleRow).order_by(PromptBundleRow.updated_at.desc(), PromptBundleRow.name.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            self._bundle_to_entry(row, version_count=await self._version_count(row.id))
            for row in rows
        ]

    async def get_bundle(self, name: str) -> PromptBundleEntry | None:
        row = await self._bundle_row_by_name(name)
        if row is None:
            return None
        return self._bundle_to_entry(row, version_count=await self._version_count(row.id))

    async def list_versions(self, bundle_name: str) -> list[PromptVersionEntry]:
        bundle = await self._bundle_row_by_name(bundle_name)
        if bundle is None:
            return []
        stmt = (
            select(PromptVersionRow)
            .where(PromptVersionRow.bundle_id == bundle.id)
            .order_by(PromptVersionRow.created_at.desc(), PromptVersionRow.version.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._version_to_entry(row, bundle_name=bundle.name) for row in rows]

    async def get_version(self, bundle_name: str, version: str) -> PromptVersionEntry | None:
        bundle = await self._bundle_row_by_name(bundle_name)
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

    async def list_traces_by_bundle(
        self,
        bundle_name: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptTraceEntry]:
        bundle = await self._bundle_row_by_name(bundle_name)
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

    async def get_trace(self, trace_id: str) -> PromptTraceEntry | None:
        row = await self.session.get(PromptTraceRow, trace_id)
        if row is None:
            return None
        return await self._trace_to_entry(row)

    async def record_trace(
        self,
        req: ChatRequest,
        *,
        provider_name: str,
        model: str | None,
        bundle_name: str = DEFAULT_BUNDLE_NAME,
        version: str = DEFAULT_VERSION,
    ) -> PromptTraceEntry:
        await self.seed_if_empty()
        bundle = await self._bundle_row_by_name(bundle_name)
        if bundle is None:
            bundle = await self._bundle_row_by_name(DEFAULT_BUNDLE_NAME)
        if bundle is None:
            raise RuntimeError("prompt bundle seed failed")
        ver = await self.get_version(bundle.name, version)
        if ver is None:
            ver = await self._create_version(bundle.id, bundle.name, version)
        snapshot = build_snapshot(
            req,
            provider_name=provider_name,
            model=model,
            bundle_name=bundle.name,
            version=ver.version,
        )
        row = PromptTraceRow(
            bundle_id=bundle.id,
            version_id=ver.id,
            conversation_id=req.conversation_id,
            provider_name=provider_name,
            model=model,
            request=self._serialize_json("request", snapshot.request),
            source_refs=self._serialize_json("source_refs", snapshot.source_refs),
            prompt_size=snapshot.prompt_size,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return await self._trace_to_entry(row)

    async def _create_version(self, bundle_id: str, bundle_name: str, version: str) -> PromptVersionEntry:
        row = PromptVersionRow(
            bundle_id=bundle_id,
            version=version,
            spec=self._serialize_json(
                "spec",
                {"bundle": bundle_name, "version": version, "layers": DEFAULT_BUNDLE_LAYERS},
            ),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._version_to_entry(row, bundle_name=bundle_name)

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

    async def _bundle_name_map(self) -> dict[str, str]:
        stmt = select(PromptBundleRow.id, PromptBundleRow.name)
        rows = (await self.session.execute(stmt)).all()
        return {row.id: row.name for row in rows}

    @staticmethod
    def _bundle_to_entry(row: PromptBundleRow, *, version_count: int = 0) -> PromptBundleEntry:
        return PromptBundleEntry(
            id=row.id,
            name=row.name,
            description=row.description,
            layers=cast(list[dict[str, Any]], json.loads(row.layers)),
            version_count=version_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version_to_entry(row: PromptVersionRow, *, bundle_name: str) -> PromptVersionEntry:
        return PromptVersionEntry(
            id=row.id,
            bundle_id=row.bundle_id,
            bundle_name=bundle_name,
            version=row.version,
            spec=cast(dict[str, Any], json.loads(row.spec)),
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
            provider_name=row.provider_name,
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
