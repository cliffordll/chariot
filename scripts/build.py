"""PyInstaller 打包驱动。

用法
----
默认打 sidecar + cli:
    uv run --group build python scripts/build.py

只打其一:
    uv run --group build python scripts/build.py --target sidecar
    uv run --group build python scripts/build.py --target cli

打完自动同步 sidecar 到 Tauri(packages/desktop/tauri/binaries/):
    uv run --group build python scripts/build.py --target sidecar --sync-sidecar

产物
----
    dist/chariot-sidecar.exe   (Tauri sidecar,stdio JSON-RPC)
    dist/chariot.exe           (CLI)

中间产物落在 `build/work/<spec-name>/`(.gitignore 已覆盖),不污染 spec 源目录。
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_DIR = _REPO_ROOT / "build"
_DIST_DIR = _REPO_ROOT / "dist"
_WORK_DIR = _BUILD_DIR / "work"

_TARGETS: dict[str, tuple[str, str]] = {
    # key → (spec 文件名, 最终 exe 名 · 用于打印和产物校验)
    "sidecar": ("chariot-sidecar.spec", "chariot-sidecar.exe"),
    "cli": ("chariot.spec", "chariot.exe"),
}

# `--target all` 默认打哪几个
_DEFAULT_TARGETS: tuple[str, ...] = ("sidecar", "cli")


def _run_pyinstaller(spec_name: str) -> None:
    spec_path = _BUILD_DIR / spec_name
    if not spec_path.exists():
        raise RuntimeError(f"spec 不存在:{spec_path}")

    # --distpath / --workpath 必须是绝对路径,否则 PyInstaller 以当前工作目录为根
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(_DIST_DIR),
        "--workpath",
        str(_WORK_DIR),
        str(spec_path),
    ]
    print(f"\n[build] $ {' '.join(cmd)}\n", flush=True)
    result = subprocess.run(cmd, cwd=_BUILD_DIR, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller 失败:{spec_name} exit={result.returncode}")


def _report(exe_name: str) -> None:
    exe_path = _DIST_DIR / exe_name
    if not exe_path.exists():
        print(f"[build] FAIL 产物未生成:{exe_path}", file=sys.stderr)
        return
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"[build] OK   {exe_path}  ({size_mb:.1f} MB)")


def _sync_sidecar() -> None:
    """调 packages/desktop/scripts/sync-sidecar.mjs 同步 sidecar.exe 到 Tauri 期待位置。"""
    script = _REPO_ROOT / "packages" / "desktop" / "scripts" / "sync-sidecar.mjs"
    if not script.exists():
        raise RuntimeError(f"sync-sidecar 脚本不存在:{script}")
    print(f"\n[build] $ node {script}\n", flush=True)
    result = subprocess.run(["node", str(script)], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"sync-sidecar 失败 exit={result.returncode}")


def main() -> int:
    # Windows 默认 stdout/stderr 是 cp1252,中文输出会 UnicodeEncodeError;reconfigure 兜底
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    parser = argparse.ArgumentParser(description="PyInstaller 打包驱动")
    parser.add_argument(
        "--target",
        choices=["sidecar", "cli", "all"],
        default="all",
        help="打哪个;all = sidecar + cli",
    )
    parser.add_argument(
        "--sync-sidecar",
        action="store_true",
        help="打完后把 dist/chariot-sidecar.exe 同步到 packages/desktop/tauri/binaries/",
    )
    args = parser.parse_args()

    targets: list[str] = list(_DEFAULT_TARGETS) if args.target == "all" else [args.target]

    # 只清要重打的 exe,不动另一个 target
    for t in targets:
        stale = _DIST_DIR / _TARGETS[t][1]
        if stale.exists():
            stale.unlink()

    for t in targets:
        spec_name, _exe_name = _TARGETS[t]
        _run_pyinstaller(spec_name)

    print("\n[build] 产物:")
    for t in targets:
        _report(_TARGETS[t][1])

    if args.sync_sidecar:
        if "sidecar" not in targets:
            print(
                "[build] --sync-sidecar 被忽略(未打 sidecar target)",
                file=sys.stderr,
            )
        else:
            _sync_sidecar()

    return 0


if __name__ == "__main__":
    sys.exit(main())
