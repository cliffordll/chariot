# 桌面端启动与本地运行指南

> 覆盖"**不走 NSIS installer**"的几种跑桌面端方式:开发模式、产 exe 直接双击、跨机分发。
> 面向:开发调试、内部 smoke、给同事发一份能立刻用的 exe。
> 真正的带签名 + 首个 Release 走 `docs/FEATURE.md` §8.3 的 CI workflow,不在本文档范围。

---

## 快速对照

| 场景 | 命令 | 产物 | 首次耗时 |
|---|---|---|---|
| 日常改代码调试 | `bun run tauri dev` | 弹出窗口 + HMR | Rust 首编 ~1-3 min |
| 产 exe 自己双击 | `bun run tauri build --no-bundle --debug` | `target/debug/chariot-desktop.exe`(~150 MB) | ~1-2 min |
| 产 exe 分发给别人 | `bun run tauri build --no-bundle` | `target/release/chariot-desktop.exe`(~50 MB) | ~3-8 min |
| 底层 Rust 编译 | `cargo build --release` | 同上 | 同上,但不含 frontend dist 自动拷贝 |

---

## 1. 前置(一次性)

```bash
# Rust toolchain(只装一次,CI 也装一次)
# 任选其一:rustup-init / Visual Studio Build Tools + Rust for Windows

# bun 前端工具(只装一次)
# https://bun.sh

# 克隆后首次,装前端依赖
bun install

# 同步 server sidecar(Python 打出来的 exe 就位到 Tauri binaries/)
uv run --group build python scripts/build.py --target server --sync-sidecar

# 等效展开(build.py 的 --sync-sidecar 就是这两步的糖):
uv run --group build python scripts/build.py --target server
bun run --filter=@chariot/desktop sync-sidecar
```

`--sync-sidecar` 做的事:把 `dist/chariot-server.exe` 拷贝到
`packages/desktop/tauri/binaries/chariot-server-<target-triple>.exe`
(例:`chariot-server-x86_64-pc-windows-msvc.exe`)。这是 Tauri 2 sidecar
的**命名约定**,缺了 Tauri 启动时 spawn 就报 "找不到 sidecar"。

> ⚠️ **server 代码改了就重跑 `--sync-sidecar`**:否则窗口跑起来用的是旧的 server
> 版本。前端 / Rust 改动不受影响。

---

## 2. 开发模式 · `tauri dev`(日常调试首选)

```bash
cd packages/desktop/tauri
bun run tauri dev
```

做了什么:

1. `beforeDevCommand` 起 vite dev server 在 `http://localhost:5173`
2. Rust 侧 `cargo build` 编译 app(**首次 1-3 分钟**,后续增量秒级)
3. 弹窗口加载 `http://localhost:5173`
4. Tauri setup 里 spawn sidecar `chariot-server.exe` → 写
   `~/.chariot/endpoint.json` → 前端 fetch `/admin/*` 走 vite proxy
   到 server

**热更新**:

- 前端改代码:Vite HMR 自动刷新窗口,无需手动
- Rust 改代码:Tauri 自动重编 + 重启 app
- Server Python 改代码:需 `Ctrl+C` 停 `tauri dev` → 重跑 `--sync-sidecar`
  → `bun run tauri dev`;或临时不走 sidecar 改用 `uv run python -m
  chariot.server`(端口会不同,但 endpoint.json 机制兼容)

---

## 3. 产 exe 不打 installer · `tauri build --no-bundle`(推荐)

适合:想要一个自包含的 exe 扔到任何地方双击就跑,但不走 NSIS 安装 / 不要 start menu 快捷方式。

```bash
cd packages/desktop/tauri
bun run tauri -- build --no-bundle           # release 模式,exe 约 50 MB
bun run tauri -- build --no-bundle --debug   # debug 模式,exe 约 150 MB,编译快 ~60%
```

> ⚠️ **`--` 不能省**。bun 1.3+ 对内置子命令名(`build` / `install` / ...)
> 的 flag 解析很强势:写 `bun run tauri build --no-bundle`,bun 会把
> `build --no-bundle` 抢去当 `bun build --no-bundle` → 报
> `Missing entrypoints. What would you like to bundle?`。`--` 把后面的
> 全部当 script 参数透传给 tauri CLI,绕开此坑。
>
> 同样的坑在 `tauri.conf.json` 的 `beforeDevCommand` /
> `beforeBuildCommand` 里:必须写 `bun run --filter=@chariot/app build`,
> 不能写 `bun --filter=@chariot/app build`(后者会被解释成 `bun build`)。

产物:

- `target/release/chariot-desktop.exe`(或 `target/debug/`)
- 前端静态资源 (HTML/CSS/JS)**内嵌进 exe**,不需单独目录
- 图标、部分 manifest 也在 exe 里

**注意**:sidecar(`chariot-server.exe`)**不**内嵌,需要单独带。见 §5 分发。

### 为什么不直接 `cargo build`

- Tauri CLI 会自动跑 `beforeBuildCommand`(`bun run --filter=@chariot/app build`)
  确保前端 dist 最新,并把资源注入
- cargo 不管前端;手动 `bun run --filter=@chariot/app build` 后再 `cargo build`
  也能,但多一步易漏

---

## 4. 极简:cargo build 直接编

```bash
# 前端先 build(cargo 不会自动做)
bun run --filter=@chariot/app build

cd packages/desktop/tauri
cargo build --release
./target/release/chariot-desktop.exe
```

绕过 tauri-cli,编译更快(省了 tauri 的 hook),但要自己保证前端 dist 同步。
适合 Rust 侧在改代码反复编的场景。

---

## 5. 跨机分发:最小目录

想把 exe 发给同事,**不经过安装**直接跑,从 `target/release/` 一起拷这两个到目标
机器任何目录(必须**同目录**):

```
my-chariot/
├── chariot-desktop.exe   # 主程序
└── chariot-server.exe    # sidecar(release build 时 Tauri 会自动从 binaries/<name>-<triple>.exe
                          #         拷到主 exe 同目录,并去掉 triple 后缀)
```

> ⚠️ **同目录是硬约束**。Tauri 2 的 `ShellExt::sidecar()` 在 release 模式下
> 只在主 exe 同目录找 `chariot-server.exe`。只移动 `chariot-desktop.exe`
> 不带 sidecar → setup hook 报 "找不到 sidecar" 返 Err → 主进程立即退出,
> UI 都来不及画 → 表现为**双击秒闪退,无任何提示**。

目标机器 Windows 10/11 64bit,不需要装 Python / Rust / bun。

**用户第一次跑需要同意 SmartScreen**(没 Authenticode 签名):点 "More info" →
"Run anyway"。想彻底去掉这个提示得走 §8.3 的完整签名 release 流程。

**数据落点**:`~/.chariot/chariot.db` + `~/.chariot/endpoint.json`
(全平台一致)。

---

## 6. 常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| 窗口白屏 + 控制台报 `port 5173 refused` | `tauri dev` 里 vite 还没起好 | 等 ~2s / 重跑;首次编译 Rust 慢,vite 提前准备好 |
| 所有 API 红条 `server unreachable` | sidecar 没起 / 版本旧 | 重跑 `--sync-sidecar` 再 `tauri dev` |
| "找不到 chariot-server sidecar" | `binaries/` 里没 exe 或命名不对 | 必须是 `chariot-server-<target-triple>.exe`,`--sync-sidecar` 会自动命对 |
| `tauri build` 报 `tauri-build-*.../permission denied` | 窗口仍开着占用 `target/` 文件 | 先关所有跑着的桌面端 + `bun run tauri dev` 再 build |
| SmartScreen 警告 | exe 没 Authenticode 签名 | "More info → Run anyway" 或走完整签名(FEATURE §8.3) |
| 关掉 app 后 `chariot-server.exe` 残留 | `--parent-pid` watcher 5s 内自退 | `tasklist \| findstr chariot-server` 等一会再看;5s 后仍在算 bug |
| debug / release exe 体积差很大 | debug 带全部符号,release 开 strip | 分发用 release;debug 自测快但别发给别人 |
| 双击 exe 秒闪退,无任何 UI | 主 exe 被单独移走,sidecar `chariot-server.exe` 不在同目录 | release/ 里两个 exe 一起拷;见 §5 |
| `bun run tauri build ...` 报 `Missing entrypoints` | bun 把 `build` 当成内置 `bun build` 子命令 | 改成 `bun run tauri -- build ...`(`--` 把后续参数透传给 tauri CLI) |
| 双击 exe 没反应 / 看不到新窗口 | 已有一个实例在跑(可能缩去托盘),`tauri-plugin-single-instance` 会拒掉第二份并把已有窗口顶到前台 | 去托盘点 Show / 任务栏找已有窗口;真要彻底退出走托盘 Exit |

---

## 7. 延伸

- FEATURE §7.1 ✅ · sidecar 启动 verification
- FEATURE §7.2 ✅ · 窗口记忆 + 托盘 + 优雅退出
- FEATURE §8.3 🟡 · 完整 NSIS installer + 签名链路(需证书,跳过中)
- `docs/guides/rust-toolchain.md` · Rust 编译工具链说明
- `docs/guides/tauri-icons.md` · 图标生成
