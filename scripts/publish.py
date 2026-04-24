"""发布工具:版本号管理 + 一键编译所有二进制。

用法
----
    uv run python scripts/publish.py check               校验 7 处版本号一致(CI 友好)
    uv run python scripts/publish.py bump 0.2.0          7 处一次性同步到指定版本号
    uv run python scripts/publish.py bump patch          自动 +0.0.1(0.1.0 → 0.1.1)
    uv run python scripts/publish.py bump minor          自动 +0.1.0(0.1.0 → 0.2.0)
    uv run python scripts/publish.py bump major          自动 +1.0.0(0.1.0 → 1.0.0)
    uv run python scripts/publish.py build               一键产 CLI + server + desktop exe
    uv run python scripts/publish.py build --installer   上述 + NSIS installer + latest.json
    uv run python scripts/publish.py tag create [--push] 打本地 tag vX.Y.Z;--push 触发 release.yml
    uv run python scripts/publish.py tag delete [--push] 删本地 tag;--push 同时删 origin

build 产物按版本号归集到 `dist/chariot-<ver>/`(三个 exe 同目录,满足桌面侧
"sidecar 必须跟主 exe 同目录"的硬约束;chariot-server.exe 一份双重身份):

    dist/
    └── chariot-<ver>/
        ├── chariot.exe                       CLI
        ├── chariot-server.exe                server / 桌面 sidecar
        ├── chariot-desktop.exe               桌面壳
        ├── Chariot_<ver>_x64-setup.exe       NSIS installer(--installer 才有)
        └── latest.json                       updater manifest(--installer 才有)

版本号 source-of-truth(7 处,bump 时同步,差一位 updater / installer 名都会乱):

    pyproject.toml                                  · Python 包
    chariot/__init__.py · __version__               · server /admin/status 输出
    package.json · packages/{app,desktop}/package.json · workspace meta
    packages/desktop/tauri/Cargo.toml               · 桌面 exe Windows 文件属性
    packages/desktop/tauri/tauri.conf.json          · NSIS installer 名 + updater latest.json

锁文件(uv.lock / bun.lock / Cargo.lock)由对应工具下次 sync 时自动更新,本脚本不动。
"""

from __future__ import annotations

import argparse
import contextlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DIST_DIR = _REPO_ROOT / "dist"

# 接受 X.Y.Z 或带 prerelease/build metadata(如 0.2.0-rc1 / 0.2.0+ci.42)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

# ──────────────────────────────────────────────────────────────────────────────
# 版本号管理
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Target:
    path: Path
    # 必须含命名 group `prefix` / `old` / `suffix`,subn 时可拼回保格式
    pattern: re.Pattern[str]
    label: str


def _multiline(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.MULTILINE)


# 三类锚:
# - .toml `[package]` / `[project]` 表内首个 `version = "..."`(行首,无缩进)
# - .py 模块级 `__version__ = "..."`(行首,无缩进)
# - .json 顶层 `"version": "..."`(2 空格缩进,文件最前几行;deps 在更深层级,正则
#   只取首次匹配 + 行首非空白小于 4 个的位置避开嵌套)
_JSON_TOP_VERSION = _multiline(r'^(?P<prefix>  "version":\s*")(?P<old>[^"]+)(?P<suffix>")')
_TOML_TOP_VERSION = _multiline(r'^(?P<prefix>version\s*=\s*")(?P<old>[^"]+)(?P<suffix>")')
_PY_DUNDER_VERSION = _multiline(r'^(?P<prefix>__version__\s*=\s*")(?P<old>[^"]+)(?P<suffix>")')

_TARGETS: list[_Target] = [
    _Target(
        _REPO_ROOT / "pyproject.toml",
        _TOML_TOP_VERSION,
        "pyproject.toml [project] version",
    ),
    _Target(
        _REPO_ROOT / "chariot" / "__init__.py",
        _PY_DUNDER_VERSION,
        "chariot/__init__.py __version__",
    ),
    _Target(
        _REPO_ROOT / "package.json",
        _JSON_TOP_VERSION,
        "package.json version",
    ),
    _Target(
        _REPO_ROOT / "packages" / "app" / "package.json",
        _JSON_TOP_VERSION,
        "packages/app/package.json version",
    ),
    _Target(
        _REPO_ROOT / "packages" / "desktop" / "package.json",
        _JSON_TOP_VERSION,
        "packages/desktop/package.json version",
    ),
    _Target(
        _REPO_ROOT / "packages" / "desktop" / "tauri" / "Cargo.toml",
        _TOML_TOP_VERSION,
        "packages/desktop/tauri/Cargo.toml [package] version",
    ),
    _Target(
        _REPO_ROOT / "packages" / "desktop" / "tauri" / "tauri.conf.json",
        _JSON_TOP_VERSION,
        "packages/desktop/tauri/tauri.conf.json version",
    ),
]


def _read_version(target: _Target) -> str:
    text = target.path.read_text(encoding="utf-8")
    match = target.pattern.search(text)
    if not match:
        raise RuntimeError(f"未在 {target.label} 匹配到版本号 pattern;请检查文件结构")
    return match.group("old")


def _all_versions() -> list[tuple[_Target, str]]:
    return [(t, _read_version(t)) for t in _TARGETS]


def _print_table(pairs: list[tuple[_Target, str]]) -> None:
    width = max(len(t.label) for t, _ in pairs)
    for t, v in pairs:
        print(f"  {t.label:<{width}}  {v}")


def _cmd_check() -> int:
    pairs = _all_versions()
    print("[publish] 当前版本号:")
    _print_table(pairs)
    versions = {v for _, v in pairs}
    if len(versions) == 1:
        print(f"\n[publish] OK · 所有 {len(pairs)} 处一致")
        return 0
    print(
        f"\n[publish] FAIL · 发现 {len(versions)} 个不同版本:{sorted(versions)}",
        file=sys.stderr,
    )
    return 1


_BUMP_LEVELS = ("patch", "minor", "major")
# 干净 X.Y.Z(无 prerelease / meta);patch/minor/major bump 仅支持这种基线
_BASE_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _next_version(current: str, level: str) -> str:
    """patch/minor/major 自动递增;只支持干净 X.Y.Z(带后缀的请用显式版本号)。"""
    match = _BASE_SEMVER_RE.match(current)
    if not match:
        raise RuntimeError(
            f"当前版本 {current!r} 含 prerelease / meta 后缀,无法自动 {level} bump;请用显式版本号"
        )
    major, minor, patch = (int(x) for x in match.groups())
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "major":
        return f"{major + 1}.0.0"
    raise RuntimeError(f"未知 bump level:{level}(预期 patch / minor / major)")


def _cmd_bump(spec: str) -> int:
    if spec in _BUMP_LEVELS:
        current = _current_version_or_die()
        try:
            new = _next_version(current, spec)
        except RuntimeError as e:
            print(f"[publish] FAIL · {e}", file=sys.stderr)
            return 2
        print(f"[publish] {spec} bump · {current} → {new}\n")
    else:
        new = spec

    if not _SEMVER_RE.match(new):
        print(
            f"[publish] 非法 semver:{new!r}"
            "(期望 X.Y.Z 可带 -rc1 / +meta 后缀,或 patch / minor / major 关键字)",
            file=sys.stderr,
        )
        return 2

    changed = 0
    for t in _TARGETS:
        text = t.path.read_text(encoding="utf-8")
        old = _read_version(t)
        if old == new:
            continue
        new_text, n = t.pattern.subn(rf"\g<prefix>{new}\g<suffix>", text, count=1)
        if n != 1:
            print(
                f"[publish] FAIL · {t.label} 替换异常(预期 1 次,实际 {n})",
                file=sys.stderr,
            )
            return 1
        t.path.write_text(new_text, encoding="utf-8")
        rel = t.path.relative_to(_REPO_ROOT)
        print(f"[publish] {rel}: {old} → {new}")
        changed += 1

    if changed == 0:
        print(f"[publish] no-op · 所有 {len(_TARGETS)} 处已是 {new}")
        return 0

    print(f"\n[publish] 已更新 {changed}/{len(_TARGETS)} 处到 {new}")
    versions = {v for _, v in _all_versions()}
    if versions != {new}:
        print(
            f"[publish] FAIL · 改完后仍不一致:{sorted(versions)}(脚本 bug,检查 _TARGETS)",
            file=sys.stderr,
        )
        return 1
    print(
        "[publish] 提醒:`uv sync && bun install` 同步锁文件;"
        "Cargo.lock 下次 cargo check/build 自动更新"
    )
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# 一键编译
# ──────────────────────────────────────────────────────────────────────────────


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """跑外部命令,失败就 raise;stdout/stderr 直通终端,便于看实时编译输出。"""
    print(f"\n[publish] $ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""), flush=True)
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败 exit={result.returncode}: {' '.join(cmd)}")


def _cargo_target_release() -> Path:
    return _REPO_ROOT / "packages" / "desktop" / "tauri" / "target" / "release"


def _copy(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    rel_src = src.relative_to(_REPO_ROOT)
    rel_dst = dst.relative_to(_REPO_ROOT)
    print(f"[publish] copy {rel_src} → {rel_dst}")


def _move(src: Path, dst: Path) -> None:
    shutil.move(str(src), str(dst))
    rel_src = src.relative_to(_REPO_ROOT)
    rel_dst = dst.relative_to(_REPO_ROOT)
    print(f"[publish] move {rel_src} → {rel_dst}")


def _build_python_exes() -> None:
    """跑 scripts/build.py 产 dist/chariot.exe + dist/chariot-server.exe + sync sidecar。"""
    _run(
        [
            "uv",
            "run",
            "--group",
            "build",
            "python",
            str(_REPO_ROOT / "scripts" / "build.py"),
            "--sync-sidecar",
        ]
    )


def _build_desktop(installer: bool) -> None:
    """跑 tauri build。带 --installer → 完整 NSIS bundle;否则 --no-bundle 只出 release exe。

    在 packages/desktop 子包目录跑(`tauri` script 在那里),beforeBuildCommand 会
    自动跑前端 `bun run --filter=@chariot/app build`。
    """
    desktop_pkg = _REPO_ROOT / "packages" / "desktop"
    cmd = ["bun", "run", "tauri", "--", "build"]
    if not installer:
        cmd.append("--no-bundle")
    _run(cmd, cwd=desktop_pkg)


def _collect(release_dir: Path, installer: bool) -> None:
    """把所有产物归集到 release_dir(dist/chariot-<ver>/)。

    - dist/chariot.exe / dist/chariot-server.exe(build.py 直接写到 dist/ root)→ move
    - target/release/chariot-desktop.exe → copy(chariot-server.exe 内容跟上面那份
      一致,move 已搬走,不再重复)
    - target/release/bundle/nsis/{Chariot_*_x64-setup.exe, latest.json} → copy(--installer)
    """
    release_dir.mkdir(parents=True, exist_ok=True)

    for name in ("chariot.exe", "chariot-server.exe"):
        src = _DIST_DIR / name
        if not src.exists():
            raise RuntimeError(f"找不到 PyInstaller 产物:{src}(build.py 是否成功?)")
        _move(src, release_dir / name)

    desktop_src = _cargo_target_release() / "chariot-desktop.exe"
    if not desktop_src.exists():
        raise RuntimeError(f"找不到桌面 exe:{desktop_src}")
    _copy(desktop_src, release_dir / "chariot-desktop.exe")

    if not installer:
        return

    nsis_dir = _cargo_target_release() / "bundle" / "nsis"
    if not nsis_dir.exists():
        raise RuntimeError(f"找不到 NSIS bundle 目录:{nsis_dir}(--installer 但 tauri 未产 bundle?)")

    # installer 文件名带版本号,glob 取唯一一份(若有多份说明残留,报错)
    setup_files = list(nsis_dir.glob("Chariot_*_x64-setup.exe"))
    if len(setup_files) != 1:
        raise RuntimeError(f"NSIS installer 数量异常:{[p.name for p in setup_files]}(预期 1 份)")
    _copy(setup_files[0], release_dir / setup_files[0].name)

    # latest.json 名固定,updater 用它比对版本
    latest_src = nsis_dir / "latest.json"
    if latest_src.exists():
        _copy(latest_src, release_dir / "latest.json")
    else:
        print(
            f"[publish] WARN · {latest_src.relative_to(_REPO_ROOT)} 不存在;"
            "updater 不会工作(检查 tauri.conf.json bundle.createUpdaterArtifacts / 签名密钥)",
            file=sys.stderr,
        )


def _summary(release_dir: Path, installer: bool) -> None:
    print(f"\n[publish] 产物清单({release_dir.relative_to(_REPO_ROOT)}/):")
    for path in sorted(release_dir.rglob("*")):
        if path.is_file():
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {path.relative_to(_REPO_ROOT)}  ({size_mb:.1f} MB)")
    if not installer:
        print(
            "\n[publish] 提示:未产 NSIS installer / latest.json;加 --installer 一键产完整发布产物"
        )


def _cmd_build(installer: bool) -> int:
    # 编译前先校验版本一致,避免出半套版本错位的产物
    pairs = _all_versions()
    versions = {v for _, v in pairs}
    if len(versions) != 1:
        print("[publish] FAIL · 版本号不一致,先跑 `publish.py check` 排错", file=sys.stderr)
        _print_table(pairs)
        return 1
    version = next(iter(versions))
    print(f"[publish] 准备编译 v{version} 全套二进制" + (" + installer" if installer else ""))

    _build_python_exes()
    _build_desktop(installer=installer)

    release_dir = _DIST_DIR / f"chariot-{version}"
    _collect(release_dir, installer=installer)
    _summary(release_dir, installer=installer)
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# git tag 管理
# ──────────────────────────────────────────────────────────────────────────────


def _current_version_or_die() -> str:
    """读 7 处版本号,要求一致;否则报错退出。"""
    pairs = _all_versions()
    versions = {v for _, v in pairs}
    if len(versions) != 1:
        print("[publish] FAIL · 版本号不一致,先跑 `publish.py check` 排错", file=sys.stderr)
        _print_table(pairs)
        sys.exit(1)
    return next(iter(versions))


def _git_capture(args: list[str]) -> subprocess.CompletedProcess[str]:
    """跑 git 并捕获 stdout/stderr;只用于探测(rev-parse / ls-remote 等),不该有交互。"""
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _git_run(args: list[str]) -> int:
    """跑 git 并直通终端;用于 tag / push 等可能交互(credential 提示)+ 用户需看实时进度
    的命令。返回 returncode。"""
    print(f"\n[publish] $ git {' '.join(args)}", flush=True)
    return subprocess.run(["git", *args], cwd=_REPO_ROOT, check=False).returncode


def _local_tag_exists(tag: str) -> bool:
    return _git_capture(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"]).returncode == 0


def _remote_tag_exists(tag: str) -> bool:
    # ls-remote 返非空行说明远端有该 tag;失败(网络等)按"未知"处理 → 当 False
    result = _git_capture(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"])
    return result.returncode == 0 and bool(result.stdout.strip())


def _cmd_tag_create(push: bool) -> int:
    version = _current_version_or_die()
    tag = f"v{version}"

    if _local_tag_exists(tag):
        print(
            f"[publish] FAIL · tag {tag} 本地已存在;若需重打先 `publish.py tag delete [--push]`",
            file=sys.stderr,
        )
        return 1

    if _git_run(["tag", tag]) != 0:
        print(f"[publish] FAIL · git tag {tag} 失败(见上方 git 输出)", file=sys.stderr)
        return 1
    print(f"[publish] tag {tag} 已在本地创建")

    if not push:
        print("[publish] 提示:加 --push 同步到 origin(会触发 release.yml)")
        return 0

    if _git_run(["push", "origin", tag]) != 0:
        print(
            f"[publish] FAIL · git push origin {tag} 失败(见上方 git 输出);"
            "本地 tag 已创建,可单独再跑 `git push origin {tag}` 重试",
            file=sys.stderr,
        )
        return 1
    print(f"[publish] tag {tag} 已推送到 origin(release.yml 应已开始跑)")
    return 0


def _cmd_tag_delete(push: bool) -> int:
    version = _current_version_or_die()
    tag = f"v{version}"

    if _local_tag_exists(tag):
        if _git_run(["tag", "-d", tag]) != 0:
            print(f"[publish] FAIL · git tag -d {tag} 失败(见上方 git 输出)", file=sys.stderr)
            return 1
        print(f"[publish] tag {tag} 本地已删除")
    else:
        print(f"[publish] WARN · tag {tag} 本地不存在,跳过本地删除", file=sys.stderr)

    if not push:
        print("[publish] 提示:加 --push 同步删远端(若远端 tag 也要清)")
        return 0

    if not _remote_tag_exists(tag):
        print(f"[publish] WARN · tag {tag} 远端不存在,跳过远端删除", file=sys.stderr)
        return 0

    if _git_run(["push", "origin", "--delete", tag]) != 0:
        print(
            f"[publish] FAIL · git push origin --delete {tag} 失败(见上方 git 输出)",
            file=sys.stderr,
        )
        return 1
    print(f"[publish] tag {tag} 已从 origin 删除(GitHub Release 需在 Releases 页手动删)")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────────


def main() -> int:
    # Windows 默认 stdout/stderr 是 cp1252,中文输出会 UnicodeEncodeError;reconfigure 兜底
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    parser = argparse.ArgumentParser(description="发布工具:版本号管理 + 一键编译所有二进制")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="校验 7 处版本号一致")

    p_bump = sub.add_parser(
        "bump",
        help="同步 7 处版本号到新值(支持显式版本号或 patch / minor / major 关键字)",
        description=(
            "把 7 处 source-of-truth 版本号同步成同一个新值。\n\n"
            "version 参数支持两种形式:\n"
            "  - 显式 semver(X.Y.Z,可带 -rc1 / +meta 后缀):直接设为该值\n"
            "  - 关键字 patch / minor / major:从当前版本号自动递增,例 0.1.0 →\n"
            "    patch ⇒ 0.1.1 · minor ⇒ 0.2.0 · major ⇒ 1.0.0(关键字模式要求\n"
            "    当前版本是干净 X.Y.Z,带后缀的请用显式版本号)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bump.add_argument(
        "version",
        help="新版本号:X.Y.Z(可带 -rc1 / +meta)或 patch / minor / major 关键字",
    )

    p_build = sub.add_parser("build", help="一键产 CLI + server + desktop exe → dist/")
    p_build.add_argument(
        "--installer",
        action="store_true",
        help="同时产 NSIS installer + updater latest.json",
    )

    p_tag = sub.add_parser("tag", help="管理 vX.Y.Z 形式的 release tag")
    p_tag_sub = p_tag.add_subparsers(dest="tag_cmd", required=True)

    p_tag_create = p_tag_sub.add_parser("create", help="基于当前版本号打本地 tag vX.Y.Z")
    p_tag_create.add_argument(
        "--push",
        action="store_true",
        help="同时推送到 origin(触发 release.yml)",
    )

    p_tag_delete = p_tag_sub.add_parser("delete", help="删除本地 tag vX.Y.Z")
    p_tag_delete.add_argument(
        "--push",
        action="store_true",
        help="同时删除 origin 上的同名 tag",
    )

    args = parser.parse_args()
    if args.cmd == "check":
        return _cmd_check()
    if args.cmd == "bump":
        return _cmd_bump(args.version)
    if args.cmd == "build":
        return _cmd_build(installer=args.installer)
    if args.cmd == "tag":
        if args.tag_cmd == "create":
            return _cmd_tag_create(push=args.push)
        if args.tag_cmd == "delete":
            return _cmd_tag_delete(push=args.push)
    parser.error(f"未知命令:{args.cmd}")  # argparse 会 sys.exit
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
