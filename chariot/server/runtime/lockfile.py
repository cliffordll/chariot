"""`~/.chariot/spawn.lock` 独占抢锁,防止并发 spawn 双开。

- 抢锁:`os.open(O_CREAT | O_EXCL | O_WRONLY)`(Linux / Windows 通用)
- 陈旧检测:读锁内 PID,psutil 判进程已死 → 当陈旧锁删除重试
- 放锁:关 fd 并删文件(全幂等)

`SpawnLock` 是名空间类,不实例化;外部用 `SpawnLock.acquire()` / `SpawnLock.release(fd)`。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import ClassVar

import psutil


class SpawnLock:
    """`~/.chariot/spawn.lock` 抢锁原语集合(不实例化)。"""

    # 公开:tests / 诊断工具可能需要手动清锁文件
    PATH: ClassVar[Path] = Path.home() / ".chariot" / "spawn.lock"

    @classmethod
    def acquire(cls) -> int:
        """抢锁;别人持有时 raise FileExistsError。返回写入了自身 PID 的 fd。"""
        cls.PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(cls.PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # 已存在 → 看是真有进程在持,还是陈旧锁
            if not cls._is_stale():
                raise
            # 陈旧 → 清掉再抢一次
            cls._force_remove()
            fd = os.open(cls.PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
        except OSError:
            os.close(fd)
            cls._force_remove()
            raise
        return fd

    @classmethod
    def release(cls, fd: int | None) -> None:
        """关 fd + 删文件;全幂等(fd=None、已关、文件不存在都 OK)。"""
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        cls._force_remove()

    @classmethod
    def _is_stale(cls) -> bool:
        """读锁文件 PID;若进程已死或文件坏格式 → 算陈旧。"""
        try:
            raw = cls.PATH.read_text(encoding="utf-8").strip()
            old_pid = int(raw)
        except (OSError, ValueError):
            return True
        return not psutil.pid_exists(old_pid)

    @classmethod
    def _force_remove(cls) -> None:
        with contextlib.suppress(FileNotFoundError):
            cls.PATH.unlink()
