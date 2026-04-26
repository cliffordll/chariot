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
