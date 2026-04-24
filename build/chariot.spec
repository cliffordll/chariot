# PyInstaller spec — chariot.exe(CLI 单文件,控制台)
# hidden imports / datas 与 chariot-server.spec 保持同构(CLI 也要能 in-process
# import SDK + DB + httpx;spawn 时只调外部 server exe,但 `chariot logs` 等命令
# 直接走 HTTP + Pydantic schema,依赖面一致)。

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

_HIDDEN = [
    "aiosqlite",
    "aiosqlite.core",
    "aiosqlite.cursor",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "greenlet",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "h11",
    "anyio._backends._asyncio",
    "sniffio._impl",
]

_DATAS = [
    ("../chariot/server/database/migrations", "chariot/server/database/migrations"),
]
_DATAS += collect_data_files("certifi")
_DATAS += copy_metadata("certifi")


a = Analysis(
    ["launch-cli.py"],
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
    name="chariot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
