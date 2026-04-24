# CLI 入口路径指南

> **文件定位**:`chariot` / `chariot-server` 命令是怎么跑起来的 · 三条独立入口路径 · 为什么顶层 `chariot/` 不需要 `__main__.py`。
> **面向**:好奇"这个 CLI 到底怎么启动"或"打包 / 安装后为啥还能用同一套代码"的自己。
> **前置**:已读过 [`cli-typer.md`](./cli-typer.md)(知道 CLI 命令怎么注册),有 Python `-m` / entry-point shim 的基础认知。

---

## 一图流

| 入口路径 | 谁走这条 | 具体命令 | 实际调用 |
|---|---|---|---|
| **A · entry-point shim** | 装了包的用户(`uv sync` / `pip install`)| `chariot status` · `chariot-server` | pyproject.toml 的 `[project.scripts]` 生成的 exe/shell shim → `chariot.cli.__main__:main` |
| **B · Python `-m` 模块** | 不装包直接开发跑 | `python -m chariot.cli status` · `python -m chariot.server` | 执行子包的 `__main__.py` |
| **C · PyInstaller bundle** | 分发给终端用户的独立 exe | `./dist/chariot.exe status` · `./dist/chariot-server.exe` | `build/launch-cli.py` / `launch-server.py` launcher |

**三条路终点相同**:都会执行 `chariot.cli.__main__:main()`(或 `chariot.server.__main__:main()`)。区别只在"怎么把 Python 解释器跑起来 + 怎么找到 main"。

---

## 一、路径 A · entry-point shim(生产用法)

### 定义

`pyproject.toml:18-20`:

```toml
[project.scripts]
chariot = "chariot.cli.__main__:main"
chariot-server = "chariot.server.__main__:main"
```

### 发生了什么

`uv sync` / `pip install` 时,**setuptools / hatchling 根据 `[project.scripts]` 生成可执行 shim**:

| 平台 | 产物 |
|---|---|
| Windows | `<venv>/Scripts/chariot.exe` + `chariot-server.exe` |
| macOS / Linux | `<venv>/bin/chariot` + `chariot-server` |

shim 内容约等于:

```python
# 伪代码,概念示意
import sys
from chariot.cli.__main__ import main
sys.exit(main())
```

打开 `<venv>/Scripts/chariot.exe` 看,是个几十 KB 的小启动器。`uv run chariot status` 本质上就是 `uv` 先激活虚拟环境 PATH,再 invoke 这个 shim。

### 特点

- **最常用**:日常开发、CI、最终用户装包跑都走这条
- 和 `python -m` 相比,**用户视角更干净**(一条命令 `chariot` vs `python -m chariot.cli`)
- 依赖 `pyproject.toml` 被正确解析 + 包真装到了 site-packages

---

## 二、路径 B · `python -m chariot.cli`(开发期直跑)

### 机制

Python 的 `-m` 会 **执行 `<package>/__main__.py`**:

```bash
python -m chariot.cli status
# 等价于:执行 chariot/cli/__main__.py,把 "status" 作为 argv[1] 传进去
```

### 两个子包各自的 `__main__.py`

- `chariot/cli/__main__.py`(见 `cli-typer.md` 的"入口"章节)
- `chariot/server/__main__.py`(server 进程,`main()` 里起 uvicorn + 抢 spawn.lock + 写 endpoint.json)

两者底部都有 `if __name__ == "__main__": main()`。`-m` 把模块当 script 跑时这个条件满足,main 被触发。

### 什么时候用

- **开发期还没装包**(或 editable install 没生效)
- **调试 entry point 错误**(跳过 shim 层)
- **PyInstaller spec 里引用**(如果用 `python -m` 方式而不是 launcher,但我们没这么做)

### 注意

**`python -m chariot` 会报错**:

```
No module named chariot.__main__; 'chariot' is a package and cannot be directly executed
```

这是有意的,见"为什么顶层不加 __main__.py"。

---

## 三、路径 C · PyInstaller 打包后的 `.exe`

### 产物

`scripts/build.py --target all` 产出:

- `dist/chariot.exe` ≈ 22 MB
- `dist/chariot-server.exe` ≈ 22 MB

两个 exe **完全独立**,不依赖系统 Python / venv / site-packages。

### launcher

`build/launch-cli.py`:

```python
"""PyInstaller 入口壳:chariot.exe。"""

from __future__ import annotations
from chariot.cli.__main__ import main

if __name__ == "__main__":
    main()
```

`build/launch-server.py` 同构指向 `chariot.server.__main__.main`。

### 为什么不直接把 `chariot/cli/__main__.py` 当 PyInstaller entry?

两个 PyInstaller 的已知坑:

1. **`__main__.py` 当 script 运行时 `__name__` 会被替换成某个生成名字**,原代码里的 `if __name__ == "__main__"` 判断可能不再成立
2. **包 import 路径歧义**:`__main__.py` 被当 script 时,Python 不会把它识别成 `chariot.cli.__main__`,内部 `from chariot.cli.xxx import ...` 可能反复 import 同一个模块两次(一次作 `__main__`,一次作 `chariot.cli.__main__`),触发各种奇怪的状态重置

**launcher 解法**:launcher 自己是一个**普通脚本**,里面用**绝对 import** `from chariot.cli.__main__ import main` 把真入口 pull 进来。`chariot.cli.__main__` 作为**被 import 的模块**正常解析,不触发上面两个问题。

### 启动开销

PyInstaller `--onefile` 模式下每次启动都要**把 bundle 解压到 `%TEMP%\_MEI...`**,首次约 1-2s。status / ping 这种秒级命令能明显感觉到。阶段 8.3 前是可接受开销;真觉得慢可以改 `--onedir`。

---

## 四、为什么顶层 `chariot/` 不加 `__main__.py`

```
chariot/
├── __init__.py        ✅ 有
├── cli/
│   └── __main__.py    ✅ 有
├── server/
│   └── __main__.py    ✅ 有
├── sdk/
├── shared/
└── __main__.py        ❌ 没有(故意的)
```

**不加的原因**:

1. **两个入口各司其职**(DESIGN §6 / §7):
   - `chariot` CLI:用户交互 + 管理命令
   - `chariot-server`:长跑 HTTP server + sidecar

   没有"默认命令"可以承担顶层 `python -m chariot`

2. **加了会误导**:
   - 如果 `chariot/__main__.py` 指向 CLI → 新人以为 `chariot` = `chariot.cli`,但 `chariot-server` 完全是另一个东西
   - 如果指向 server → 更离谱

3. **entry-point shim 已经给了清晰的两个命令**(`chariot` / `chariot-server`),再加个 `python -m chariot` 的灰色地带没有增量价值

---

## 五、为什么短名 `chariot` 给 CLI,长名 `chariot-server` 给 daemon

### 机制层:两个命令是平行关系

`pyproject.toml:18-20` 定义的 **是两个独立 shim**,不是父子关系:

```toml
chariot        = "chariot.cli.__main__:main"      # 短名 → CLI
chariot-server = "chariot.server.__main__:main"   # 长名 → daemon
```

`uv run chariot` 按名字 `chariot` 查第一个 shim;`uv run chariot-server` 按名字 `chariot-server` 查第二个。**没有"默认"这个概念,也没有路由解析**,就是查表。

### 命名层:对齐 Unix 惯例

终端用户直接敲的命令拿短名,daemon 拿长名(或加 `d` / `-server` 后缀):

| 工具 | CLI(短) | daemon(长) |
|---|---|---|
| git | `git` | `git-daemon` |
| docker | `docker` | `dockerd` |
| postgres | `psql` | `postgres` |
| chariot | **`chariot`** | **`chariot-server`** |

### 设计层:为什么不合成一个 `chariot cli` / `chariot server`?

三条硬理由(项目决策):

1. **PyInstaller 独立打两个 exe**(阶段 6.1):`dist/chariot.exe` 和 `dist/chariot-server.exe` 各 22 MB。合并成一个需要把 uvicorn / fastapi 塞进 CLI,体积翻到 40+ MB,且 CLI 每次冷启动都要 import 整套 server 依赖,慢 1-2s
2. **生命周期语义分离**:server 是 long-running daemon(有 PID、绑端口、写 endpoint.json、graceful shutdown);CLI 是 one-shot 短命。一个命令两种 lifetime 会让 `chariot status` 查谁、`chariot stop` 停谁、server 挂了要不要自动拉起等问题全乱套
3. **Tauri sidecar 直接 spawn `chariot-server.exe`**:`packages/desktop/tauri/src/lib.rs` 里 `app.shell().sidecar("chariot-server")` 靠的是独立二进制。合成一个命令后 sidecar 要多写一层 `"server"` 子命令参数,无收益

### 验证

```bash
ls .venv/Scripts/chariot*.exe
# 两个独立二进制:
#   chariot.exe           CLI 入口
#   chariot-server.exe    daemon 入口
```

每个都是几十 KB 的 shim,内容本质:

```python
from chariot.<cli|server>.__main__ import main
sys.exit(main())
```

---

## 六、三条路径结果对比(事实核对表)

| 问题 | A · shim | B · `-m` | C · exe |
|---|---|---|---|
| 需要装包? | ✅ `uv sync` | ❌ 直接跑 | ❌ 独立 |
| 需要 venv PATH? | ✅(`uv run` 自动)| ✅ | ❌ |
| 启动速度 | 快(< 100ms)| 快 | **慢(首次 1-2s onefile 解压)** |
| 用户常用度 | ★★★★★ | ★★ | ★★★(发布后) |
| 推荐用于 | 日常开发 / CI | 调试 entry shim | 最终用户分发 |

---

## 七、常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| `chariot: command not found` | venv PATH 没激活 / 包没装 | `uv sync` + 用 `uv run chariot ...` |
| `No module named chariot.__main__` | 错跑了 `python -m chariot` | 改成 `python -m chariot.cli` 或 `python -m chariot.server` |
| `dist/chariot.exe` 提示"找不到 DLL" | PyInstaller hidden imports 漏某个 C 扩展 | 在 `build/chariot.spec` 的 `hiddenimports` 里补 |
| `chariot --help` 弹一堆 Rich 渲染乱码 | Windows GBK 终端 | 切 Windows Terminal / 设 `chcp 65001` |
| entry shim 改了 `pyproject.toml` 但没生效 | 改完 `[project.scripts]` 需要重新装包 | `uv sync --reinstall` |

---

## 八、加一个新的二进制入口(比如 `chariot-util`)

极少这么做,但万一要做:

1. 新建 `chariot/util/__main__.py`,含 `def main(): ...` + `if __name__ == "__main__": main()`
2. `pyproject.toml` 的 `[project.scripts]` 加一行:
   ```toml
   chariot-util = "chariot.util.__main__:main"
   ```
3. `uv sync` 生成新 shim
4. 打包:`build/launch-util.py` + `build/chariot-util.spec` + `scripts/build.py` 的 `_TARGETS` 加一条
5. 三条路径同时可用

---

## 九、参考

- Python `-m` 机制:<https://docs.python.org/3/using/cmdline.html#cmdoption-m>
- `[project.scripts]` 规范:<https://packaging.python.org/en/latest/specifications/entry-points/>
- PyInstaller onefile vs onedir:<https://pyinstaller.org/en/stable/operating-mode.html>
- 项目入口文件:
  - `chariot/cli/__main__.py`
  - `chariot/server/__main__.py`
  - `build/launch-cli.py` / `launch-server.py`
- 打包驱动:`scripts/build.py`
