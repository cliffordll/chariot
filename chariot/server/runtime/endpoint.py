"""`~/.chariot/endpoint.json` 数据模型 + 文件读写原语。

- `EndpointBase`:pydantic v2 BaseModel,承载 url / token / pid 三字段,带类型校验
- `EndpointFile`:名空间类,暴露 `PATH` / `read()` / `write()` / `delete()` 文件操作

Server 启动写 url / token / pid,客户端读来发现 server。
写入用 `.tmp` → `os.replace` 保证原子,避免客户端读到半截文件。
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ValidationError


class EndpointBase(BaseModel):
    """endpoint.json 的字段定义(pydantic,带字段校验)。"""

    url: str
    token: str
    pid: int


class EndpointFile:
    """`~/.chariot/endpoint.json` 文件读写原语集合(不实例化)。

    所有成员都是 classmethod / 类常量,外部用 `EndpointFile.<method>()` 调用。
    """

    # 公开:sdk / cli / tests 可能要读路径用于日志 / 手动清理 / fixture
    PATH: ClassVar[Path] = Path.home() / ".chariot" / "endpoint.json"
    # 内部:`.tmp` 中间文件,原子替换用
    _TMP_PATH: ClassVar[Path] = PATH.parent / (PATH.name + ".tmp")

    @classmethod
    def write(cls, *, url: str, token: str, pid: int) -> None:
        """原子写入 endpoint.json(先 `.tmp` 再 `os.replace`)。"""
        cls.PATH.parent.mkdir(parents=True, exist_ok=True)
        ep = EndpointBase(url=url, token=token, pid=pid)
        cls._TMP_PATH.write_text(ep.model_dump_json(indent=2), encoding="utf-8")
        os.replace(cls._TMP_PATH, cls.PATH)

    @classmethod
    def delete(cls) -> None:
        """幂等删除;文件不存在不报错。"""
        with contextlib.suppress(FileNotFoundError):
            cls.PATH.unlink()

    @classmethod
    def read(cls) -> EndpointBase | None:
        """读 endpoint.json;文件不存在 / JSON 损坏 / 字段缺失或类型错返回 None。"""
        try:
            raw = cls.PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        try:
            return EndpointBase.model_validate(data)
        except ValidationError:
            return None
