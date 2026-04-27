# Chariot

> **Self-evolving intelligence, in motion.** · 驾驭智能,向前。

Chariot 是一个**本机跑的智能体 (agent) server**。对外接 Anthropic Messages
协议(`POST /v1/messages`),server 自己生成响应。

默认自带 `MockModel`(本地 echo,零外部依赖);通过 `Model` 接口可以挂真实后端
(Anthropic / 本地 llama / 自研)或加进化循环。

> **0.2.0 起单协议**:chariot 只对外暴露 `/v1/messages`。OpenAI 兼容性请通过外部
> 转换器接入(见下方 [OpenAI 客户端怎么接](#openai-客户端怎么接))。架构变更详见
> [`docs/DESIGN.md`](docs/DESIGN.md);0.1.0 三协议平等的旧设计归档在
> [`docs/history/0.1.0/`](docs/history/0.1.0/)。

## Quick start

```bash
# 0. 一次性装依赖
uv sync
bun install
```

### CLI / server(最轻量,无需 Rust)

```bash
uv run python -m chariot.server            # 终端 A:起 server
uv run chariot chat "hello"                # 终端 B:一次性对话
uv run chariot chat                        # 或进 REPL
uv run chariot logs -f                     # 看请求流水
```

### Tauri 桌面壳(带 UI)

首次需要打 PyInstaller sidecar(Tauri 靠它起 server;只需做一次,之后改 Python
代码要重打):

```bash
uv run --group build python scripts/build.py --target server --sync-sidecar
```

然后:

```bash
bun run --filter=@chariot/desktop tauri dev
```

首次会编 Rust,**5-15 分钟**属正常;之后增量编译秒级。弹出窗口后 Dashboard /
Chat / Logs 三页可用。

### Web 前端(Vite dev server,浏览器访问)

不用桌面壳、只想快速迭代前端:

```bash
uv run python -m chariot.server            # 终端 A:server
bun run --filter=@chariot/app dev          # 终端 B:Vite at http://localhost:5173
```

完整首次启动(含 Rust toolchain / MSVC / 常见报错排查)见
[`docs/guides/first-run.md`](docs/guides/first-run.md)。

## Architecture

```
CLI / UI / Anthropic SDK / claude code
        │  POST /v1/messages
        ▼
Controller  →  Agent.handle(body)
                    ↓
                Model.respond(body, *, stream)
                    ↓
              MockModel (default · local echo)
              AnthropicModel (透传到 Anthropic API)
              future: LocalLlama / 自研 ...
```

三层通过两条窄接口解耦,新增一个模型或一条进化逻辑不穿层。契约细节见
[`docs/DESIGN.md`](docs/DESIGN.md) §5。

## 接真实 Anthropic 模型

> **0.3.0 起**模型配置不再走 `~/.chariot/config.toml` 文件,改存 chariot 内置
> SQLite。所有 model entries 通过 Models 页 / `chariot model` CLI / admin API
> 增删改查。首次启动会 seed 一条 `mock` entry,开箱可用。

默认走 `MockModel`(本地 echo)。要让 chariot 真打到 Anthropic Messages API,
**两种方式二选一**:

### (A) 在 Models 页加(推荐)

```bash
uv run python -m chariot.server          # 起 server
bun run --filter=@chariot/app dev         # 或者 Tauri 桌面壳:见上面 Quick start
```

打开 Models tab → `[+ Add]` → 表单填 :
- name: `claude`(任意 user-friendly id)
- type: `anthropic`
- model: `claude-opus-4-5`
- api_key: `sk-ant-...`(或留空走 env)
- api_key_env: `ANTHROPIC_API_KEY`(默认,可省)
- base_url: 留空默认 `https://api.anthropic.com`

加完去 Chat 页右上角下拉切到 `claude` —— Send 一条试试。失败可在 Models 页对该
entry 点 `[Test]` 跑探针,看到具体错码(`upstream_auth_failed` / `upstream_unreachable`
/ ...)。

### (B) CLI 一条搞定

```bash
uv run chariot model add --name claude --type anthropic \
    -o model=claude-opus-4-5 \
    -o api_key=sk-ant-...

uv run chariot model use claude          # 切 active(持久化到 DB)
uv run chariot model probe claude        # 探针验通断
```

或不传 `api_key`,走 env:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."     # PowerShell:$env:ANTHROPIC_API_KEY="..."
uv run chariot model add --name claude --type anthropic -o model=claude-opus-4-5
uv run chariot model use claude
```

### 行为细节

- 客户端 body 里写啥 `model` 都会被替换成 entry 的 `options.model`(chariot 是单一身份代理)
- 上游 401 / 403 → 502 `upstream_auth_failed`(检查 entry 的 api_key);429 透传;5xx → 502
- 流式:`stream: true` 直接透传上游 SSE 字节,中途断开靠断 TCP 通知客户端
- 没切 active(seed 后默认 mock)/ entry 不存在 → 走 MockModel fallback(开箱可用)
- ⚠️ **直填 api_key 安全提示**:密钥落地 `~/.chariot/chariot.db`(SQLite 文件)。
  务必把这个目录排除在版本库 / 备份 / 同步之外

## OpenAI 客户端怎么接

chariot 不内置 OpenAI ↔ Anthropic 协议翻译。如需用 OpenAI 客户端调 chariot,
推荐架一层成熟的转换代理(把 chariot 当 Anthropic 后端配置即可):

- **[LiteLLM](https://github.com/BerriAI/litellm)** —— 把 chariot 配成 anthropic
  provider,对外仍暴露 OpenAI 兼容端点
- **[claude-code-router](https://github.com/musistudio/claude-code-router)** ——
  专门给 claude code 客户端做 routing 的轻量代理
- **[oneapi](https://github.com/songquanpeng/one-api)** —— 多 LLM 协议聚合网关

为什么不内置:协议翻译矩阵(schema + SSE × 三协议互译)是块独立工作,这些专门的
项目做得比 chariot 自己写好。chariot 的差异化在 Agent 层(后续版本的多轮记忆 /
工具调用 / 进化循环),不在协议适配。

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12+ · FastAPI · SQLAlchemy 2.x async · aiosqlite · Typer |
| Frontend | React · TypeScript · Vite · Tailwind · shadcn/ui |
| Desktop shell | Tauri 2.x (Rust) |
| Package managers | uv (Python) · bun (frontend / Tauri workspace) |
| Packaging | PyInstaller single exe (as Tauri sidecar) |

## Docs

| File | Purpose |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | 当前版本架构(0.3.0)— Controller / Agent / Model 分层 + 模型管理 DB 化 + Registry |
| [`docs/FEATURE.md`](docs/FEATURE.md) | 当前版本任务清单(0.3.0:模型配置 DB 化 + Models 页 CRUD) |
| [`docs/history/`](docs/history/) | 历史版本 DESIGN / FEATURE 归档(每个发布版本一份冻结快照) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 0.3.0+ 方向(多轮记忆 / 工具调用 / 进化循环) |
| [`docs/guides/first-run.md`](docs/guides/first-run.md) | **First-time setup** — tools, deps, sidecar, launch |
| [`docs/guides/`](docs/guides/) | Developer guides (CLI, DB, Tauri, uv, etc.) |
| [`CLAUDE.md`](CLAUDE.md) | Claude session conventions (project-level) |

## Status

**0.3.0 ✅** — 模型配置全面 DB 化:`~/.chariot/config.toml` 真源被 chariot 内置
SQLite 替代(`models` / `settings` 表)。Models tab 提供 add / edit / delete /
duplicate UI;CLI 同步加 `chariot model add/edit/rm/duplicate`。`chariot config
init/show` 子命令组废弃。0.2.x 老用户:旧 `~/.chariot/config.toml` 不再被读取,
首次启动会 seed 一条 `mock` entry 开箱可用,真模型自行在 Models 页加。

**0.2.6 ✅** — 模型探针(`POST /admin/models/{name}/probe` + Models 页 [Test])
+ Chat 页高级采样参数 UI(temperature / top_p / max_tokens 滑杆)。

**0.4.0+** 方向:Agent 层加多轮对话记忆 / 工具调用 / 自我进化循环 —— 详见
[`docs/ROADMAP.md`](docs/ROADMAP.md)。新加真实后端只需写一个 `chariot/server/model/<name>.py`
+ `@ModelRegistry.register("xxx")` 一行装饰器。
