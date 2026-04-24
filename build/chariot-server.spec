# PyInstaller spec — chariot-server.exe(单文件,控制台)
# 由 scripts/build.py 调起;直接跑 `pyinstaller build/chariot-server.spec` 也可以。

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

# 保守 hidden imports:PyInstaller 静态分析扫不到动态 import / dispatch 的模块
_HIDDEN = [
    # DB 驱动(SA async dialect 靠字符串 dispatch)
    "aiosqlite",
    "aiosqlite.core",
    "aiosqlite.cursor",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "greenlet",
    # uvicorn loops / protocols(按 --loop / --http 值 dispatch)
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "h11",
    # httpx 的 async backend / sniffer
    "anyio._backends._asyncio",
    "sniffio._impl",
]

# 静态资源:schema migrations 必须跟着可执行走
_DATAS = [
    ("../chariot/server/database/migrations", "chariot/server/database/migrations"),
]
# certifi 的 CA bundle(httpx → TLS 握手);copy_metadata 一并拉,避免 SDK 侧版本探测失败
_DATAS += collect_data_files("certifi")
_DATAS += copy_metadata("certifi")


a = Analysis(
    ["launch-server.py"],
    pathex=[],
    binaries=[],
    datas=_DATAS,
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="chariot-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Windows windowed 子系统:spawn 时不弹黑框。stdout / stderr 被 OS 丢弃,
    # 所以日志必须走 logger + logs 表(见 runtime/logger.py · service/log_writer.py);
    # CLI 观察实时日志用 `chariot logs -f`,UI 用 Logs 页。
    # 开发模式 `python -m chariot.server` 不走 exe,stdout 正常。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
