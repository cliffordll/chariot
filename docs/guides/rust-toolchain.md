# Rust 工具链安装指南(Windows · Tauri 2 开发)

> **文件定位**:从零开始装 Rust + MSVC,给 `packages/desktop/tauri/` 的 Tauri 2 外壳准备编译环境。
> **面向**:第一次在 Windows 上碰 Rust,或刚到新机器要搭环境的自己。
> **平台**:Windows 11(Windows 10 同理;macOS/Linux 装法见官网,本文不覆盖)。

---

## 一图流

| 组件 | 作用 | 体积 | 必需? |
|---|---|---|---|
| **MSVC Build Tools** | Rust 在 Windows 上用 `link.exe` 链接 C 库 | ~3-5 GB | ✅ |
| **rustup** | Rust 工具链管理器(官方) | ~300 MB | ✅ |
| **rustc + cargo** | 编译器 + 构建工具(rustup 自动拉) | ~1-2 GB | ✅ |
| **WebView2 Runtime** | Tauri webview 渲染引擎 | 已自带 | — Windows 11 自带 |

**合计约 5-8 GB 磁盘**。首次 Tauri 项目 `cargo build` 再加 ~2-3 GB 编译缓存。

---

## 一、装 MSVC(先装这个)

Rust 在 Windows 链接阶段用 Microsoft 的 `link.exe`。rustup 检测到没 MSVC 会中断安装,**所以要先装 MSVC**。

### 方式 A · winget(推荐)

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools --silent --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

### 方式 B · 官网手装

1. 打开 <https://visualstudio.microsoft.com/visual-cpp-build-tools/>
2. 下 "Build Tools for Visual Studio 2022"。实际为 vs_BuildTools.exe
3. 运行安装器 → 勾 **"Desktop development with C++"**(右侧 checkbox 保留默认)
4. 点"安装",等 15-30 分钟

### 已装过 VS Community / Pro?

打开 "Visual Studio Installer" → "Modify" → 确认已勾 **"Desktop development with C++"**。没勾就加上,不用另装 Build Tools。

---

## 二、装 Rustup

### 方式 A · winget

```powershell
winget install --id Rustlang.Rustup
```

### 方式 B · 官网

1. 打开 <https://rustup.rs/>
2. 下 `rustup-init.exe`,双击
3. 命令行里选 **`1` (Proceed with installation, default)**,一路回车

rustup 会自动装默认 toolchain:`stable-x86_64-pc-windows-msvc`。

---

## 三、验证(**关键:新开一个 shell**)

rustup 改 PATH 只在新进程生效。**关掉当前 bash / PowerShell,重新打开**,再跑:

```bash
cargo --version
# 预期:cargo 1.82.0 (xxxxxxxxx 2024-xx-xx)

rustc --version
# 预期:rustc 1.82.0 (...)

rustup show
# 预期包含:
#   Default host: x86_64-pc-windows-msvc
#   stable-x86_64-pc-windows-msvc (default)
```

**必须确认** `Default host` 是 `x86_64-pc-windows-msvc`(不是 `gnu` 变体)。如果不是:

```bash
rustup default stable-x86_64-pc-windows-msvc
```

---

## 四、可选 · 换盘放缓存(省 C 盘)

cargo / rustup 默认在 `%USERPROFILE%\.cargo` 和 `%USERPROFILE%\.rustup`,都在 C 盘。Tauri 项目首次编译缓存 2-3 GB,想挪到 D 盘:

```powershell
setx CARGO_HOME "D:\cargo"
setx RUSTUP_HOME "D:\rustup"
```

**重开 shell** 后生效。装 rustup **之前**设好最省事;装完再设需要手动把 `.cargo` / `.rustup` 目录移过去。

---

## 五、常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| `cargo: command not found`(装完 rustup 后) | 旧 shell 的 PATH 没更新 | 关了重开 shell |
| 编译到链接阶段报 `link.exe not found` | MSVC 没装好 / 没勾 C++ 工作负载 | 用 VS Installer 加上 "Desktop development with C++" |
| 首次 `cargo build` 巨慢 | Tauri 依赖多,首次要下载 + 编译几百个 crate | 正常,5-10 分钟;后续增量很快 |
| `error: toolchain 'stable-x86_64-pc-windows-gnu' is not installed` | rustup 默认选了 gnu 变体 | `rustup default stable-x86_64-pc-windows-msvc` |
| winget 提示找不到 package | 系统太旧 / winget 未更新 | 走方式 B 手装 |
| `cargo build` 报 `failed to run custom build command for 'tauri-build'` | 通常是 MSVC 环境变量没就绪 | 开 "x64 Native Tools Command Prompt" 试一次;或重启电脑让环境变量全面生效 |

---

## 六、升级

```bash
rustup update
```

每半年 stable 版会发新,一条命令搞定。

---

## 七、完全卸载(留档)

```bash
rustup self uninstall
```

如果还想删 MSVC:VS Installer → Build Tools 右边的 "More" → "Uninstall"。

---

## 八、最小可用检查清单

```bash
# 在新 shell 里
cargo --version     # ✓ 有版本号
rustc --version     # ✓ 有版本号
rustup show         # ✓ Default host: x86_64-pc-windows-msvc
```

三条都对,就可以回去做 `packages/desktop/tauri/` 了。

---

## 参考

- Rustup 官方:<https://rustup.rs/>
- Tauri 2 前置条件:<https://v2.tauri.app/start/prerequisites/>
- MSVC Build Tools:<https://visualstudio.microsoft.com/visual-cpp-build-tools/>
