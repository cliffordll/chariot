# PyInstaller spec — chariot.exe(CLI 单文件,控制台)
# CLI 0.6.5+ 是纯库模式:in-process AIAgent + DB + httpx,无 fastapi / uvicorn 依赖。

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

_HIDDEN = [
    "aiosqlite",
    "aiosqlite.core",
    "aiosqlite.cursor",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "greenlet",
    "anyio._backends._asyncio",
    "sniffio._impl",
]

_DATAS = [
    ("../chariot/database/migrations", "chariot/database/migrations"),
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
