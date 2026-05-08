# Chariot

> **Self-evolving intelligence, in motion.** · 驾驭智能,向前。

Chariot 是**本机跑的自演化 CLI agent**:核心是一个可 import 的 Python 库
(`chariot/agent/`),提供协议无关的 `AIAgent` + `ChatRequest` / `ChatEvent` IR
(三层都跟 Claude Messages API 1:1)。Surface 层平铺多种入口 —— `cli/`(本地终端
直调) / `sidecar/`(Tauri 桌面壳的 stdio JSON-RPC 子进程),后续会加
`gateways/`(Telegram / Discord)等。

默认自带 `MockProvider`(本地 echo,零外部依赖);通过 `BaseProvider` 可挂
真实后端 —— 当前内置 `AnthropicProvider`(走 Claude Messages API),0.7.0+ 会加
`OpenAIProvider` / `LocalLlamaProvider` 等。

## Quick start

```bash
# 一次性装依赖
uv sync
bun install
```

### CLI(最轻量,无需 Rust)

```bash
uv run chariot chat "hello"                # 一次性对话
uv run chariot chat                        # 进 REPL
uv run chariot logs                        # 看请求流水
```

CLI 直接 in-process 调用 `AIAgent`,无 server / 无 IPC、不需要任何运行时握手
文件。

### Tauri 桌面壳(带 UI)

首次需要打 PyInstaller sidecar(Tauri 通过 stdio JSON-RPC 跟它通信;只需做一次,
之后改 Python 代码要重打):

```bash
uv run --group build python scripts/build.py --target sidecar --sync-sidecar
```

然后:

```bash
bun run --filter=@chariot/desktop tauri dev
```

首次会编 Rust,**5-15 分钟**属正常;之后增量编译秒级。窗口弹出后 Chat / Models
/ Tools / Conversations / Logs 五页可用。

### Web 前端(Vite dev server,浏览器调试)

不用桌面壳、只想快速迭代前端的 UI 部分:

```bash
bun run --filter=@chariot/app dev          # Vite at http://localhost:5173
```

注意:Vite 浏览器模式下 `Tauri.invoke` 不可用,数据访问层会报错。这套用法仅
适合纯样式 / 路由迭代;真正跑 chat / models 还是走 `tauri dev`。

完整首次启动(含 Rust toolchain / MSVC / 常见报错排查)见
[`docs/guides/first-run.md`](docs/guides/first-run.md)。

## Architecture

```
                ┌─────────────────────────────────────────┐
 Surface 层     │ cli/   sidecar/   gateways/(0.8.0+)  │
                └──────────────────┬──────────────────────┘
                                   │  调 AIAgent.run(req)
                                   ▼
 Agent 内核        AIAgent  →  AgentLoop  →  Provider.generate
                       │              │            │
                       └─ ConvoRepo   └─ ToolRegistry
                                           │
                                           └─ BaseTool.execute
                                              read_file / list_dir / shell_exec / http_get

 Provider 层      AnthropicProvider / MockProvider / 0.7.0+ OpenAI / LocalLlama
                       │   每实例 build 一次 ClientSpec,httpx client 由
                       │   ClientCache 进程级共享(LRU 16,asyncio.Lock 并发安全)
                       ▼
 Wire             Claude Messages API(SSE)/ 其它(0.7.0+ 内部翻译成 Claude IR)
```

层间窄接口:Surface 只调 `AIAgent.run`,不知道 Provider;`AIAgent` 只调
`BaseProvider.generate`,不知道 wire format;非 Claude Provider(0.7.0+)在
内部翻译,不污染内核。详见 [`docs/DESIGN.md`](docs/DESIGN.md) §5–§7。

## Claude 形态 IR(三层 1:1)

| Layer | Type | 跟 Claude 关系 |
|---|---|---|
| 请求 IR | `ChatRequest`(`@dataclass(frozen=True)`) | 14 字段跟 Claude Messages API request body 1:1 |
| 流式事件 IR | `ChatEvent`(`@dataclass(frozen=True)`,`kind` literal 10 种) | 8 种 Claude 原生 SSE event(`message_start` / `content_block_*` / `message_*` / `ping` / `error`) + 2 种 chariot 自注(`tool_result` / `stream_done`) |
| 落库 messages | `messages.content` JSON | 跟 Claude `messages[].content` blocks list 同形 |

`AnthropicProvider` 几乎透传(body 用 `dataclasses.asdict()` 拼);其它非
Claude Provider 在自身内部翻译,翻译只发生在 Provider 内部,不污染内核 / 不污
染落库 / 不污染 surface。

## 接真实 Claude 模型

默认 seed 一条 `mock` provider entry。要让 chariot 真打到 Anthropic Messages
API,**两种方式二选一**:

### (A) 在 Models 页加(推荐)

打开 Tauri 应用 → Models tab → `[+ Add]` → 表单填:

- name: `claude`(任意 user-friendly id;`chat --provider` 就写这个)
- type: `anthropic`
- model: `claude-opus-4-5`(透传给上游 API)
- api_key: `sk-ant-...`(或留空走 env)
- api_key_env: `ANTHROPIC_API_KEY`(默认,可省)
- base_url: 留空默认 `https://api.anthropic.com`

加完去 Chat 页选这个 provider 发一条试试。失败可在 Models 页对该 entry 点
`[Test]` 跑探针,看到具体错码(`upstream_auth_failed` / `upstream_unreachable`
/ ...)。

### (B) CLI 一条搞定

```bash
uv run chariot provider add --name claude --type anthropic \
    -o model=claude-opus-4-5 \
    -o api_key=sk-ant-... \
    -p temperature=0.5

uv run chariot provider probe claude        # 探针验通断
```

或不传 `api_key`,走 env:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uv run chariot provider add --name claude --type anthropic -o model=claude-opus-4-5
uv run chariot provider probe claude
```

⚠️ **直填 api_key 安全提示**:密钥落地 `~/.chariot/chariot.db`(SQLite 文件)。
务必把这个目录排除在版本库 / 备份 / 同步之外。

### Per-call override

CLI 支持单次调用覆盖 provider 配置,不改 DB:

```bash
uv run chariot chat --provider claude --model claude-haiku-4-5 "hi"
uv run chariot chat --provider claude --base-url https://proxy.test/ "hi"
uv run chariot chat --provider claude --api-key sk-... "hi"
```

`--model` 走 per-call wire 覆盖(不重建 Provider);`--base-url` / `--api-key`
重建 Provider(ClientCache 自动命中或新建 client)。

## 多轮对话 + 工具调用

### 多轮对话(`--convo`)

CLI:

```bash
uv run chariot chat --convo new "你好"          # 新建会话
uv run chariot convo list                        # 列所有会话
uv run chariot chat --convo <ulid> "继续之前的"  # 接着聊
uv run chariot convo show <ulid>                 # 看完整 messages
uv run chariot convo rename <ulid> "调试 SQL"     # 改标题
uv run chariot convo rm <ulid>                   # 删
```

GUI Chat 页内置侧栏:`+ New` / 选 / rename / delete;首发自动创建 ULID。

### 工具调用

内置 4 条 fixture(默认全部 disabled,name + type 不可改):

| name | 用途 | 关键 options |
|---|---|---|
| `read_file` | 读本地文件 | `max_bytes`(截断阈值) |
| `list_dir` | 列目录 | `recursive`(是否递归) |
| `shell_exec` | 跑命令 | `workdir` / `timeout_sec`;**不过 shell**,直接 `subprocess_exec`(免 quoting + 免 shell metachar 注入) |
| `http_get` | HTTP GET | `allowed_domains`(域白名单) / `max_bytes` |

GUI Tools 页或 CLI 启用:

```bash
uv run chariot tool list
uv run chariot tool enable shell_exec
uv run chariot tool config http_get -o 'allowed_domains=["api.example.com"]'
uv run chariot tool disable shell_exec
```

`AgentLoop` 在每轮请求注入所有 enabled tools 的 schema,LLM 产 `tool_use`
block → 执行 → 拼 `tool_result` 回 LLM,直到收敛。max iterations 由 env
`CHARIOT_MAX_TOOL_ITER` 控制(默认 10)。

⚠️ **shell_exec / http_get 无沙箱**:工具以 chariot 进程身份直接跑命令 / 发
请求。本机单用户场景下可接受;别把 chariot 暴露到公网或多用户共享环境,也别把
`workdir` 指到敏感目录。

## Tech stack

| Layer | Choice |
|---|---|
| Core | Python 3.12+ · SQLAlchemy 2.x async · aiosqlite · httpx · Typer · prompt_toolkit |
| Surface IPC | stdio JSON-RPC(sidecar / 后续 acp / mcp 共享 `chariot/rpc/jsonrpc.py`) |
| Frontend | React 19 · TypeScript · Vite 6 · Tailwind 4 · shadcn/ui |
| Desktop shell | Tauri 2.x (Rust) |
| Package managers | uv (Python) · bun (frontend / Tauri workspace) |
| Packaging | PyInstaller single exe(`chariot-sidecar.exe` as Tauri sidecar) |

## Docs

| File | Purpose |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | 当前版本架构(0.6.5)— AIAgent 库化 + Claude 形态 IR + 多 surface |
| [`docs/FEATURE.md`](docs/FEATURE.md) | 当前版本任务清单(0.6.0 主线 + 0.6.5 patch) |
| [`docs/history/`](docs/history/) | 历史版本 DESIGN / FEATURE 归档(每个发布版本一份冻结快照) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 0.7.0+ 方向(OpenAIProvider / Memory / Skills / Gateways / Cron) |
| [`docs/guides/first-run.md`](docs/guides/first-run.md) | **First-time setup** — tools, deps, sidecar, launch |
| [`docs/guides/`](docs/guides/) | Developer guides (CLI, DB, Tauri, uv, etc.) |
| [`CLAUDE.md`](CLAUDE.md) | Claude session conventions (project-level) |

## Status

**0.6.5 ✅** — 架构修正,绝不留技术债:`AIAgent` 撤单例改 `AgentRegistry` per-session
缓存;`BaseProvider` 不再持 httpx client,`ClientSpec` + `ClientCache` 进程级
共享;`ChatRequest.model` per-call 字段加回支持 wire override。

**0.6.0 ✅** — 架构定位扭转:HTTP server 形态退役,`AIAgent` 库化(可 import
库);Claude 形态 IR(`ChatRequest` / `ChatEvent` / 落库 messages 三层 1:1);
CLI 直接 in-process 调内核;Tauri 切到 stdio JSON-RPC sidecar
(`chariot/sidecar/`);protocol-agnostic 错误体系(`ProviderError` /
`ConvoLockTimeout` / `ToolExecutionError`);`chariot/rpc/jsonrpc.py` 框架共享
给后续 acp / mcp surface。

**0.7.0+** 方向:`OpenAIProvider`(协议翻译只在 Provider 内部) / Memory / Skills
(自演化基础) / Gateways(Telegram + Discord) / Cron 调度 / 多 AIAgent 实例 /
LocalLlamaProvider —— 详见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
