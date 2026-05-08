# PyInstaller spec — chariot-sidecar.exe(0.6.5 S.9 起 · 单文件,控制台)。
#
# 跟 chariot-server.spec 区别:
# - entry:`launch-sidecar.py` → `chariot.sidecar` 模块;无 fastapi / uvicorn
# - hidden imports:撤 uvicorn.* / h11 / fastapi 相关(sidecar 不跑 HTTP);
#   保留 aiosqlite / sqlalchemy / certifi
# - migrations data:沿用 0.6.0 后路径 `chariot/database/migrations`(不是
#   旧的 chariot/server/database/migrations)
# - **`console=True`**:sidecar 通过 stdin/stdout 跟父进程通信,console 子系统
#   保证 sys.stdout / sys.stderr 在子进程模式下正常工作。`console=False`
#   时 PyInstaller bootloader 重定向 stdout 到 NULL,sidecar 写不出 RPC 帧
#   → 直接坏。Tauri spawn 子进程时不会弹黑窗(子进程继承父进程 console
#   状态,Tauri app 没 console 所以 child 也无 console attach,但 stdio
#   handles 是 pipe 不是 console handle,write 仍然到 pipe 正常)。

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
    # httpx 的 async backend / sniffer(provider 上游 HTTP)
    "anyio._backends._asyncio",
    "sniffio._impl",
]

# 静态资源:schema migrations 必须跟着可执行走
_DATAS = [
    ("../chariot/database/migrations", "chariot/database/migrations"),
]
# certifi 的 CA bundle(httpx → TLS 握手)
_DATAS += collect_data_files("certifi")
_DATAS += copy_metadata("certifi")


a = Analysis(
    ["launch-sidecar.py"],
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
    name="chariot-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True:必须保留 stdio 通道(sidecar 通过 stdin/stdout 跟 Tauri 父进程
    # 通信)。详见 spec 顶部注释。
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
