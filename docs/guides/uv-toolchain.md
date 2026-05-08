# uv 三兄弟使用手册

> **文件定位**：`.python-version` / `pyproject.toml` / `uv.lock` 这三个文件的**日常用法速查**。
> **面向**：第一次用 uv 的开发者、或者忘了命令想查一下的自己。
> **前置**：已在本机装好 `uv`（装法见 <https://docs.astral.sh/uv/getting-started/installation/>）。

---

## 一图流

| 文件 | 回答什么问题 | 谁写 | 要 commit 吗 |
|---|---|---|---|
| `.python-version` | 用哪版 Python？ | 你手写（一行） | ✅ |
| `pyproject.toml` | **想要**哪些依赖？ | 你手写（列包名和元信息） | ✅ |
| `uv.lock` | **实际装了**哪些精确版本？ | uv 自动生成/更新 | ✅ |

**三个都要提交到 git**。缺一个，别人拉下来就跑不起来或跑出不同结果。

---

## 快速入门：从零到能跑

在一个空目录里，依次跑：

```bash
# 1. 钉 Python 版本
uv python pin 3.12
# 自动生成 .python-version,内容就一行 "3.12"

# 2. 初始化项目
uv init --package
# 自动生成 pyproject.toml + src/<name>/__init__.py + README.md

# 3. 装依赖(例:装 httpx 和 typer)
uv add httpx typer
# 自动更新 pyproject.toml 的 dependencies + 生成/更新 uv.lock
# 同时在 .venv/ 里装好包

# 4. 跑代码
uv run python -c "import httpx; print(httpx.__version__)"
```

跑完之后目录里会有：

```
.python-version    ← 第 1 步生成
pyproject.toml     ← 第 2 步生成,第 3 步更新
uv.lock            ← 第 3 步生成
.venv/             ← 第 3 步生成(虚拟环境,不进 git)
src/<name>/        ← 第 2 步生成
```

---

## 按场景查命令

### 场景 A：克隆仓库后，第一次装环境

```bash
git clone <repo>
cd <repo>
uv sync
```

uv 会：
1. 读 `.python-version` → 如本机没装 3.12，自动拉下来
2. 读 `uv.lock` → 按 lock 里的**精确版本**装到 `.venv/`
3. 你直接 `uv run python -m xxx` 就能跑

**不用你手动建虚拟环境**，`uv sync` 全包了。

### 场景 B：加一个新依赖

```bash
uv add sqlalchemy              # 普通依赖
uv add 'sqlalchemy[asyncio]'   # 带 extras
uv add --dev pytest            # 只在开发时需要(不进 runtime 依赖)
uv add 'httpx>=0.28'           # 带版本约束
```

每跑一次，`pyproject.toml` 和 `uv.lock` **都自动更新**。

### 场景 C：删除一个依赖

```bash
uv remove sqlalchemy
```

### 场景 D：升级依赖

```bash
# 升级单个包
uv lock --upgrade-package httpx

# 升级所有包到 pyproject.toml 里版本约束允许的最新
uv lock --upgrade
uv sync                         # 让 .venv 和新 lock 对齐
```

**注意**：升级是主动动作。平时 `uv sync` 只会按 lock 装，不会偷偷升。

### 场景 E：换 Python 版本

```bash
uv python pin 3.13
uv sync
```

`.python-version` 改成 3.13，`.venv` 会被重建。

### 场景 F：虚拟环境坏了，想重装

```bash
rm -rf .venv
uv sync
```

安全无损——所有信息都在 `pyproject.toml` 和 `uv.lock` 里，`.venv` 只是**派生产物**。

### 场景 G：跑一次性命令

```bash
uv run ruff check .
uv run pytest
uv run chariot status
```

`uv run` = "在 `.venv` 的 context 里跑这条命令"，不用手动 activate 虚拟环境。

### 场景 H：进入交互式 shell（启用虚拟环境）

```bash
# Windows (bash / git bash)
source .venv/Scripts/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate
```

进去之后 `python` / `pytest` / `ruff` 可以直接跑，不用前缀 `uv run`。退出 `deactivate`。

---

## 三兄弟分工详解

### `.python-version`

**内容**：就一行版本号。

```
3.12
```

**作用**：告诉 uv（和 pyenv、rye 等其他工具）这个项目用哪版 Python。

**修改方式**：

```bash
uv python pin 3.13             # 推荐(会校验版本存在)
```

或直接编辑文件（不推荐，容易填错）。

---

### `pyproject.toml`

**内容**：项目元信息 + 依赖声明 + 工具配置。典型样子：

```toml
[project]
name = "chariot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite",
    "typer",
]

[project.scripts]
chariot = "chariot.cli.__main__:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio",
    "ruff",
    "pyright",
]

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**关键段落含义**：

| 段落 | 做什么 |
|---|---|
| `[project]` | 项目元信息（名字、版本、Python 最低版） |
| `[project].dependencies` | **运行时**需要的包 |
| `[project.scripts]` | 装完之后提供哪些命令行入口 |
| `[dependency-groups].dev` | 只在开发时需要的包（测试、lint、类型检查器） |
| `[tool.xxx]` | 各工具的配置（ruff、pytest、pyright 等都能读这里） |

**修改方式**：

- 加/删依赖 → 用 `uv add` / `uv remove`，**别手改 `dependencies` 列表**
- 改元信息、入口、工具配置 → 直接编辑

---

### `uv.lock`

**内容**：大几百上千行机器生成的 TOML，记录整个依赖树每个包的精确版本 + 哈希。

**举例**（片段）：

```toml
[[package]]
name = "httpx"
version = "0.28.1"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "anyio" },
    { name = "certifi" },
    { name = "httpcore" },
    { name = "idna" },
]
wheels = [
    { url = "...", hash = "sha256:abc..." },
]
```

**作用**：

1. **复现性**：保证你和队友、CI、生产环境装出**一模一样**的依赖
2. **安全性**：hash 校验，防止包被中间人篡改
3. **审计性**：想知道装了什么？grep `uv.lock` 一目了然

**修改方式**：**不要手改**。永远通过 `uv add` / `uv remove` / `uv lock --upgrade` 这些命令让 uv 自己维护。

---

## 常见问题

### Q1：为啥要把 `uv.lock` 提交到 git？

因为它是**复现性的唯一保证**。

- 没 lock：`pyproject.toml` 里写 `httpx>=0.28`，今天装 `0.28.1`，一个月后装 `0.29.0`，行为可能变了
- 有 lock：所有人、所有环境、永远装 `0.28.1`,直到你主动升级

CI、生产、本地**必须一致**，lock 是实现这个一致性的文件。

### Q2：`pyproject.toml` 和老式的 `requirements.txt` 什么关系？

`requirements.txt` 是老方案，你可以把它理解成"`uv.lock` 的简化、非标准版本"——只记包名和版本，没哈希、没依赖树结构、不跨工具。

新项目**不用** `requirements.txt`，直接用 `pyproject.toml` + `uv.lock` 这套。

如果临时要导出给不支持 uv 的工具用：

```bash
uv export --format requirements-txt > requirements.txt
```

### Q3：别人给我的 `uv.lock` 和我的合并冲突了怎么办？

最无脑也最可靠的做法：

```bash
# 1. 接受一方的 pyproject.toml(通常选 main 分支的)
git checkout --theirs pyproject.toml
# 2. 删掉冲突的 lock
rm uv.lock
# 3. 重新生成
uv lock
# 4. 提交
git add pyproject.toml uv.lock
git commit
```

**不要手工解 `uv.lock` 里的冲突**——它是机器生成的，手改风险高，让 uv 根据当前 `pyproject.toml` 重新算一份就好。

### Q4：`uv sync` 和 `uv lock` 有什么区别？

| 命令 | 改 `uv.lock` | 改 `.venv` |
|---|---|---|
| `uv lock` | ✅ | ❌ |
| `uv sync` | ❌（用现有的 lock） | ✅ |
| `uv add / remove` | ✅ | ✅ |

**记忆**：`lock` 管"写账本"，`sync` 管"按账本实际装货"。

### Q5：`.venv` 目录要不要进 git？

**不要**。它完全是派生物，几百 MB 的二进制，跨平台还不兼容。

`.gitignore` 里加这两行：

```
.venv/
__pycache__/
```

---

## 速查命令表

| 我想…… | 跑什么 |
|---|---|
| 首次装环境 | `uv sync` |
| 加依赖 | `uv add <name>` |
| 加开发依赖 | `uv add --dev <name>` |
| 删依赖 | `uv remove <name>` |
| 升级单个包 | `uv lock --upgrade-package <name>` |
| 升级全部 | `uv lock --upgrade && uv sync` |
| 换 Python 版本 | `uv python pin <ver> && uv sync` |
| 重建虚拟环境 | `rm -rf .venv && uv sync` |
| 跑命令 | `uv run <cmd>` |
| 看已装包 | `uv tree` |
| 看过期包 | `uv lock --check` |

---

## 延伸阅读

- uv 官方文档：<https://docs.astral.sh/uv/>
- PEP 621（`pyproject.toml` 标准）：<https://peps.python.org/pep-0621/>
- PEP 735（`[dependency-groups]`）：<https://peps.python.org/pep-0735/>
