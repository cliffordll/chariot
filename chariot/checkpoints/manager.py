"""CheckpointManager —— 三件套 snapshot + rollback(B5 wave 3)。

三件套设计:
- **git stash push -u**:保留 unsaved 改动(包括 untracked);仓库非 git
  目录 / 无改动 → no-op,视为成功
- **SQLite backup**:`sqlite3.Connection.backup()`(online,不阻塞 chariot 自身
  写 DB)→ 落 `~/.chariot/checkpoints/<name>.sqlite`
- **config tarball**:压缩 `~/.chariot/config.yaml` + `.env` → `<name>.tgz`(不存在
  的文件跳过;目标文件是 tar.gz)

封装策略(CLAUDE.md ⭐):
- 单类 `CheckpointManager` 编排全部三步;模块级零自由函数
- `CheckpointResult` / `RollbackResult` frozen dataclass,字段一一对应三件套
- 持有 `sessionmaker` + `AuditHookManager`;每次 create / rollback / delete
  开自己的 session
- `db_path` / `checkpoint_dir` 在 `__init__` 时解析

best-effort 错误处理:每一段独立 try / except,失败的段在 `payload['errors']`
+ rollback `errors` 里报,不阻其它段。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sqlite3
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from chariot.repos.checkpoint_repo import CheckpointEntry, CheckpointRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.audit import AuditHookManager


@dataclass(frozen=True)
class CheckpointResult:
    """`create` 的三段结果。落进 `checkpoints.payload`。"""

    git_ok: bool
    git_stash_ref: str | None  # stash 引用(e.g. "stash@{0}");None = skipped/failed
    db_ok: bool
    db_path: str | None  # backup 落盘路径
    config_ok: bool
    config_path: str | None  # tarball 落盘路径
    errors: list[str]


@dataclass(frozen=True)
class RollbackResult:
    """`rollback` 的三段结果。"""

    git_ok: bool
    db_ok: bool
    config_ok: bool
    restored: list[str]  # 实际恢复的文件 / 目标(给 audit 留证)
    errors: list[str]


class CheckpointManager:
    """git stash + sqlite backup + config tarball 三件套。

    用法::

        mgr = CheckpointManager(
            sessionmaker=sm,
            db_path=Path("~/.chariot/chariot.db").expanduser(),
            checkpoint_dir=Path("~/.chariot/checkpoints").expanduser(),
            audit_hooks=agent.audit_hooks,
        )
        entry = await mgr.create("before_refactor")
        # ... 跑改动 ...
        result = await mgr.rollback(entry.id)
    """

    DEFAULT_KIND = "full"

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        db_path: Path,
        checkpoint_dir: Path,
        config_files: list[Path] | None = None,
        cwd: Path | None = None,
        audit_hooks: AuditHookManager | None = None,
    ) -> None:
        from chariot.audit import AuditHookManager as _AuditHookManager

        self._sessionmaker = sessionmaker
        self._db_path = db_path
        self._checkpoint_dir = checkpoint_dir
        self._cwd = cwd or Path.cwd()
        # 默认装 ~/.chariot/config.yaml + .env;调用方可覆盖
        if config_files is None:
            home = db_path.parent  # ~/.chariot/
            config_files = [home / "config.yaml", home / ".env"]
        self._config_files = config_files
        self._audit_hooks = audit_hooks or _AuditHookManager(None)

    # ---- create ----

    async def create(self, name: str) -> CheckpointEntry:
        """跑三件套 snapshot,写 `checkpoints` 行 + audit。"""
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        result = await self._snapshot_three(name)
        payload = {
            "git_ok": result.git_ok,
            "git_stash_ref": result.git_stash_ref,
            "db_ok": result.db_ok,
            "db_path": result.db_path,
            "config_ok": result.config_ok,
            "config_path": result.config_path,
            "errors": result.errors,
        }
        async with self._sessionmaker() as session:
            entry = await CheckpointRepo(session).create(
                name=name,
                kind=self.DEFAULT_KIND,
                target=None,
                payload=payload,
            )
        await self._audit_hooks.record_checkpoint_create(
            checkpoint_id=entry.id,
            name=entry.name,
            kind=entry.kind,
            target=entry.target,
        )
        return entry

    async def _snapshot_three(self, name: str) -> CheckpointResult:
        """跑三段;每段独立 try / except。"""
        errors: list[str] = []
        git_ok, stash_ref = False, None
        try:
            git_ok, stash_ref = await self._git_stash(name)
        except Exception as exc:  # pragma: no cover - best-effort
            errors.append(f"git: {exc}")

        db_ok, db_backup_path = False, None
        try:
            db_backup_path = await self._sqlite_backup(name)
            db_ok = db_backup_path is not None
        except Exception as exc:  # pragma: no cover - best-effort
            errors.append(f"db: {exc}")

        config_ok, config_path = False, None
        try:
            config_path = await self._config_tarball(name)
            config_ok = config_path is not None
        except Exception as exc:  # pragma: no cover - best-effort
            errors.append(f"config: {exc}")

        return CheckpointResult(
            git_ok=git_ok,
            git_stash_ref=stash_ref,
            db_ok=db_ok,
            db_path=str(db_backup_path) if db_backup_path else None,
            config_ok=config_ok,
            config_path=str(config_path) if config_path else None,
            errors=errors,
        )

    # ---- rollback ----

    async def rollback(self, checkpoint_id: str) -> RollbackResult:
        """三段反向。失败的段在 `errors` 里报,不阻其它段。"""
        async with self._sessionmaker() as session:
            entry = await CheckpointRepo(session).get_entry(checkpoint_id)
        if entry is None:
            result = RollbackResult(
                git_ok=False,
                db_ok=False,
                config_ok=False,
                restored=[],
                errors=[f"checkpoint {checkpoint_id!r} 不存在"],
            )
            await self._audit_hooks.record_rollback(
                checkpoint_id=checkpoint_id,
                ok=False,
                errors=result.errors,
            )
            return result
        payload = entry.payload
        errors: list[str] = []
        restored: list[str] = []

        git_ok = await self._git_apply(payload.get("git_stash_ref"), errors, restored)
        db_ok = await self._sqlite_restore(payload.get("db_path"), errors, restored)
        config_ok = await self._config_untar(payload.get("config_path"), errors, restored)

        ok_all = git_ok and db_ok and config_ok
        result = RollbackResult(
            git_ok=git_ok,
            db_ok=db_ok,
            config_ok=config_ok,
            restored=restored,
            errors=errors,
        )
        await self._audit_hooks.record_rollback(
            checkpoint_id=checkpoint_id,
            ok=ok_all,
            restored=restored,
            errors=errors,
        )
        return result

    # ---- git ----

    async def _git_stash(self, name: str) -> tuple[bool, str | None]:
        """git stash push -u -m "chariot-checkpoint: <name>";返 (ok, stash_ref)。

        非 git 仓库 / 无改动:返 `(True, None)` —— 当作 no-op 成功。
        """
        rc, _, _ = await self._run(["git", "-C", str(self._cwd), "rev-parse", "--git-dir"])
        if rc != 0:
            return True, None  # 非 git 仓库,视为 no-op 成功
        rc, out, _ = await self._run(["git", "-C", str(self._cwd), "status", "--porcelain"])
        if rc != 0:
            return False, None
        if not out.strip():
            return True, None  # 干净,无需 stash
        message = f"chariot-checkpoint: {name}"
        rc, _, _ = await self._run(["git", "-C", str(self._cwd), "stash", "push", "-u", "-m", message])
        if rc != 0:
            return False, None
        rc, out, _ = await self._run(["git", "-C", str(self._cwd), "stash", "list", "-n", "1", "--format=%gd %gs"])
        if rc != 0:
            return True, None
        first = out.strip().splitlines()[0] if out.strip() else ""
        ref = first.split()[0] if first else None
        return True, ref

    async def _git_apply(
        self,
        stash_ref: str | None,
        errors: list[str],
        restored: list[str],
    ) -> bool:
        """git stash apply <ref>(keep stash);若 ref 是 None,视为 no-op 成功。"""
        if not stash_ref:
            return True
        rc, _, err = await self._run(["git", "-C", str(self._cwd), "stash", "apply", stash_ref])
        if rc != 0:
            errors.append(f"git stash apply {stash_ref}: {err.strip()}")
            return False
        restored.append(f"git:{stash_ref}")
        return True

    # ---- sqlite ----

    async def _sqlite_backup(self, name: str) -> Path | None:
        """SQLite Connection.backup() → `<dir>/<name>.sqlite`。

        run_in_executor 跑同步 sqlite3 API;`Connection.backup` 是 online,
        不会阻塞 chariot 自己对 DB 的写(底层走 SQLite backup API)。
        """
        if not self._db_path.exists():
            return None
        target = self._checkpoint_dir / f"{name}.sqlite"

        def _do_backup() -> None:
            src = sqlite3.connect(str(self._db_path))
            try:
                dst = sqlite3.connect(str(target))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

        await asyncio.to_thread(_do_backup)
        return target

    async def _sqlite_restore(
        self,
        backup_path: str | None,
        errors: list[str],
        restored: list[str],
    ) -> bool:
        """把 backup 文件原地覆盖 db_path。

        注意:这里要求外层调用方已关闭 DB engine(否则文件被锁)。CLI 实现里
        `chariot checkpoint rollback` 是独立进程,无锁问题;sidecar 调时需
        先 release 所有 agent + dispose engine。
        """
        if not backup_path:
            return True  # 无 backup,视为 no-op
        path = Path(backup_path)
        if not path.exists():
            errors.append(f"sqlite backup not found: {backup_path}")
            return False
        try:
            await asyncio.to_thread(shutil.copy2, str(path), str(self._db_path))
            restored.append(f"db:{self._db_path}")
            return True
        except OSError as exc:
            errors.append(f"sqlite restore: {exc}")
            return False

    # ---- config tarball ----

    async def _config_tarball(self, name: str) -> Path | None:
        """把 `~/.chariot/config.yaml` + `.env` 打 tar.gz → `<dir>/<name>.tgz`。

        所有候选文件都不存在 → 返 None(no-op)。
        """
        present = [p for p in self._config_files if p.exists()]
        if not present:
            return None
        target = self._checkpoint_dir / f"{name}.tgz"

        def _do_tar() -> None:
            with tarfile.open(target, "w:gz") as tar:
                for p in present:
                    tar.add(p, arcname=p.name)

        await asyncio.to_thread(_do_tar)
        return target

    async def _config_untar(
        self,
        tarball_path: str | None,
        errors: list[str],
        restored: list[str],
    ) -> bool:
        """解 tarball → 覆盖到 `~/.chariot/`(每个 arcname 写到 config 目录)。"""
        if not tarball_path:
            return True
        path = Path(tarball_path)
        if not path.exists():
            errors.append(f"config tarball not found: {tarball_path}")
            return False
        config_dir = self._db_path.parent

        def _do_untar() -> list[str]:
            written: list[str] = []
            with tarfile.open(path, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    target = config_dir / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fobj = tar.extractfile(member)
                    if fobj is None:
                        continue
                    target.write_bytes(fobj.read())
                    written.append(str(target))
            return written

        try:
            files = await asyncio.to_thread(_do_untar)
            restored.extend(f"config:{f}" for f in files)
            return True
        except OSError as exc:
            errors.append(f"config untar: {exc}")
            return False

    # ---- delete ----

    async def delete(self, checkpoint_id: str) -> bool:
        """删 checkpoint 行 + 落盘文件(sqlite / tgz);git stash 保留(避免误删用户改动)。

        返 True = 删了 row;False = checkpoint 不存在。
        """
        async with self._sessionmaker() as session:
            entry = await CheckpointRepo(session).get_entry(checkpoint_id)
            if entry is None:
                return False
            payload = entry.payload
            for key in ("db_path", "config_path"):
                path_str = payload.get(key)
                if not path_str:
                    continue
                path = Path(path_str)
                if path.exists():
                    # best-effort 删:文件占用 / 权限不够 → 留给用户手工清,不阻 row 删
                    with contextlib.suppress(OSError):
                        path.unlink()
            await CheckpointRepo(session).delete(checkpoint_id)
        return True

    # ---- subprocess helper ----

    @staticmethod
    async def _run(cmd: list[str]) -> tuple[int, str, str]:
        """跑 subprocess,返 (returncode, stdout, stderr)。"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode if proc.returncode is not None else -1
        return (
            rc,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
