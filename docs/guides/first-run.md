# 首次启动指南

> **面向**:克隆仓库后第一次把 chariot 跑起来。
> **覆盖**:前置工具链 → 依赖安装 → CLI 跑通 → sidecar 打包 → Tauri 桌面 dev 验证。
> **不覆盖**:日常开发 / 打 release(见 [`desktop-run.md`](./desktop-run.md))。

---

## 零、前置工具链(一次性,永久用)

在 PATH 里,下面 4 样缺一不可(只跑 CLI 可以省掉 Rust):

| 工具 | 版本 | 作用 | 安装 |
|---|---|---|---|
| Python | 3.12+ | 内核 / sidecar / 测试 | <https://www.python.org/> |
| uv | 最新 | Python 包管理 + 虚拟环境 | `pip install uv` 或 <https://docs.astral.sh/uv/> |
| bun | 1.3+ | 前端 + Tauri workspace 包管理(只跑 CLI 不需要) | <https://bun.sh> |
| Rust toolchain | 1.77+ + MSVC | 编译 Tauri 桌面壳(只跑 CLI 不需要) | `rustup-init.exe` + Visual Studio Build Tools(Windows) |

自检:

```bash
python --version     # Python 3.12.x
uv --version         # uv 0.x.x
bun --version        # 1.3.x
cargo --version      # cargo 1.77+
```

Rust + MSVC 的 Windows 细节见 [`rust-toolchain.md`](./rust-toolchain.md);uv 细节见 [`uv-toolchain.md`](./uv-toolchain.md)。

---

## 一、依赖安装

在 repo 根目录执行,顺序不能反:

```bash
# 1. Python 虚拟环境 + 依赖(生成 .venv/,读 pyproject.toml + uv.lock)
uv sync

# 2. 前端 + Tauri workspace 依赖(生成 packages/*/node_modules/;只跑 CLI 可省)
bun install
```

`uv sync` 大概几秒到几十秒;`bun install` 第一次会拉 Tauri / React / Vite 等,
一两分钟。

---

## 二、CLI 跑通(最快验证装对了)

CLI 直接 in-process 调用 `AIAgent`,**不需要任何 sidecar / 不需要 server**。

```bash
uv run chariot status                     # 看版本 / DB / providers / tools
uv run chariot chat "hello"               # 一次性对话(默认 mock provider,本地 echo)
uv run chariot chat                       # 进 REPL
uv run chariot logs                       # 看请求流水
```

DB 落 `~/.chariot/chariot.db`(SQLite),首次运行自动建表 + seed 一条 `mock`
provider entry。要接真实 Claude 见 README "接真实 Claude 模型" 段。

---

## 三、(可选)打 sidecar(PyInstaller)

桌面 / Tauri 模式靠 `chariot-sidecar.exe` 提供后端(stdio JSON-RPC 子进程)。
如果只跑 CLI,这步可以跳。

```bash
uv run --group build python scripts/build.py --target sidecar --sync-sidecar
```

参数说明:

- `--target sidecar` 只打 sidecar,不打 CLI(也可 `--target cli` 单独打 / 省略
  默认两个都打)
- `--sync-sidecar` 打完自动 `cp dist/chariot-sidecar.exe
  packages/desktop/tauri/binaries/chariot-sidecar-<triple>.exe`

耗时 30 秒到 1 分钟。成功后:

```bash
ls dist/chariot-sidecar.exe                                      # 独立 exe
ls packages/desktop/tauri/binaries/chariot-sidecar-*.exe         # sidecar 副本
```

两个都在就行。

---

## 四、起桌面 dev 模式(Tauri)

```bash
bun run --filter=@chariot/desktop tauri dev
```

**首次会编 Rust,卡在 "Compiling tauri-plugin-* / tauri v2.x.x" 是正常的,预估
5-15 分钟**,之后增量编译秒级。

成功标志:

1. 终端最后出现 `Running BeforeDevCommand (...)`(Vite)+ Tauri 主进程 spawn
   sidecar 子进程的日志
2. 弹出标题为 "Chariot" 的窗口
3. 窗口里能看到 Chat / Models / Tools / Conversations / Logs 五页

---

## 五、不起桌面,只跑静态检查 + 测试

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # 格式
uv run pyright chariot/          # 类型
uv run pytest -q                 # 测试
```

CI (`.github/workflows/ci.yml`) 跑这四条,本地全绿才 push。

---

## 六、常见首次启动报错

| 症状 | 根因 | 修复 |
|---|---|---|
| `RuntimeError: spec 不存在: build/chariot-sidecar.spec` | `build/` 目录缺失或 spec 文件没就位 | `build/` 里应有 `chariot.spec` / `chariot-sidecar.spec` / `launch-cli.py` / `launch-sidecar.py`,缺哪补哪(这些是**源文件**,入 git 的) |
| `ModuleNotFoundError: chariot` | `uv sync` 没跑,或没激活 `.venv` | 回到 `uv sync`;跑命令前缀 `uv run` 让 uv 自动选 venv |
| `bun: command not found: tauri` | `bun install` 没跑,`@tauri-apps/cli` 不在 node_modules | `bun install` |
| `error: linker 'link.exe' not found` / `error[E0432]` 一大堆 | Windows MSVC 链接器缺失 | 装 Visual Studio Build Tools(勾 "Desktop development with C++") |
| 桌面窗口弹出但功能挂(Models 列表空、Chat 不响应) | sidecar exe 没就位,Rust 侧 spawn 失败 | `python scripts/build.py --target sidecar --sync-sidecar` 重来 |
| Vite 报 `Port 5173 is in use` | 其他 Vite / dev server 占着 | 关掉那个;或改 `packages/app/vite.config.ts` 的 `server.port` + `packages/desktop/tauri/tauri.conf.json` 的 `devUrl` 到同一个新端口 |
| `UnicodeEncodeError: 'cp1252' codec can't encode ...` | Windows 默认终端编码 | 项目代码里已兜底(`build.py` reconfigure + `PYTHONIOENCODING=utf-8` fixture);仍见到升级到项目最新代码 |

---

## 七、最小可用检查清单

一次性全跑完,六条都绿就是装对了:

```bash
# 1. 工具链齐
python --version && uv --version && bun --version && cargo --version

# 2. 装依赖
uv sync && bun install

# 3. CLI 跑通(最快验证)
uv run chariot status
uv run chariot chat "hello"        # 用 mock provider,echo 回来即可

# 4. 打 sidecar(桌面用)
uv run --group build python scripts/build.py --target sidecar --sync-sidecar
ls dist/chariot-sidecar.exe packages/desktop/tauri/binaries/chariot-sidecar-*.exe

# 5. 静态检查 + 测试
uv run ruff check . && uv run ruff format --check . && uv run pyright chariot/
uv run pytest -q

# 6. 起桌面(首次等 Rust 编译 5-15 min)
bun run --filter=@chariot/desktop tauri dev
```

全过,装完。

---

## 相关指南

- [`desktop-run.md`](./desktop-run.md) — 桌面端不同启动方式(dev / 无 bundle exe / cargo 直编)
- [`tauri-icons.md`](./tauri-icons.md) — logo.svg → 全套桌面 / web 图标
- [`cli-entrypoints.md`](./cli-entrypoints.md) — chariot CLI 入口与子命令
- [`database.md`](./database.md) — SQLite schema + 迁移
- [`rust-toolchain.md`](./rust-toolchain.md) — Rust + MSVC 装配细节
- [`uv-toolchain.md`](./uv-toolchain.md) — uv / Python 环境细节
