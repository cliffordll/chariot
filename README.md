# Chariot

> **Self-evolving intelligence, in motion.** · 驾驭智能,向前。

Chariot 是一个**本机跑的智能体 (agent) server**。通过 HTTP 对话 —— 三种业界主流 API shape
(Anthropic Messages / OpenAI Chat Completions / OpenAI Responses)都是一等公民,
server 自己生成响应,不代理到外部 LLM。

默认自带 `MockModel`(本地 echo,零外部依赖);未来通过同一个 `Model` 接口可以挂
真实后端(Anthropic / OpenAI / 本地 llama / 自研)或加进化循环。

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
CLI / UI / HTTP client
        │  POST /v1/messages  |  /v1/chat/completions  |  /v1/responses
        ▼
Controller  →  Agent.handle(protocol, body)
                    ↓
                Model.respond(protocol, body, *, stream)
                    ↓
              MockModel (v0 default · local echo)
              future: AnthropicAdapter / OpenAIAdapter / LocalLlama / ...
```

三层通过两条窄接口解耦,新增一个模型或一条进化逻辑不穿层。契约细节见
[`docs/DESIGN.md`](docs/DESIGN.md) §5。

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
| [`docs/DESIGN.md`](docs/DESIGN.md) | Architecture reference — Controller / Agent / Model layering |
| [`docs/FEATURE.md`](docs/FEATURE.md) | Phased task list (v0 ✅ baseline / v1 real models / v2 agent evolution) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Post-v0 directions |
| [`docs/guides/first-run.md`](docs/guides/first-run.md) | **First-time setup** — tools, deps, sidecar, launch |
| [`docs/guides/`](docs/guides/) | Developer guides (CLI, DB, Tauri, uv, etc.) |
| [`CLAUDE.md`](CLAUDE.md) | Claude session conventions (project-level) |

## Status

**v0 skeleton ✅** — Controller / Agent / Model 三层就位;`MockModel` 是默认
(本地 echo,零外部依赖)。v1 通过 `Model` 接口挂真实模型后端,v2 在 Agent
层加对话记忆 / 工具调用 / 自我进化循环。
