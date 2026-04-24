# Chariot 功能清单

> 分阶段任务。heading emoji 标进度:✅ 完成 · 🟡 跳过 · ⏸️ 暂缓 · 未打表示待做。
> 每步完成后按 CLAUDE.md 的 gate 流程:跑验证 → 用户确认 → 打进度标记 → 下一步。

---

## v0:骨架 + MockModel

### ✅ 1.1 项目脚手架
从 Rosetta 项目迁移的 Python + Tauri + React 骨架;包管理 uv + bun;CI(GitHub Actions)、lint(ruff)、typecheck(pyright)、pytest 框架就位。

### ✅ 1.2 FastAPI app + SQLite + migrations
`chariot/server/app.py` lifespan;`database/session.py` + `migrations/001_init.sql`(只含 logs 表);`PRAGMA user_version` 迁移机制。

### ✅ 1.3 Model / Agent 双层解耦
- `chariot/server/model/base.py`:`Model` Protocol
- `chariot/server/model/mock.py`:`MockModel` —— 三协议手写 echo(非流 + SSE)
- `chariot/server/agent.py`:`Agent` 类 + `build_agent()` / `init_agent()` / `get_agent()` / `reset_agent()`

### ✅ 1.4 Dataplane + Logs
- `POST /v1/messages`、`/v1/chat/completions`、`/v1/responses` 三端点 → `get_agent().handle()`
- Agent 记一条 log(status / model / latency_ms / error)
- `GET /admin/logs` + since 游标

### ✅ 1.5 Admin / Runtime
- `GET /admin/ping` / `/admin/status` / `POST /admin/shutdown`
- status 返回 `{version, uptime_ms, model, url}`(model 来自 `agent.model.name`)
- `GET /admin/stats`(period 聚合)

### ✅ 1.6 CLI
- `chariot status / start / stop / chat / logs / stats`
- `chat` 支持 `--protocol` / `--model` / `--max-tokens`,REPL 和 one-shot 两种
- `logs` 支持 `-f` follow + polling since

### ✅ 1.7 SDK
- `ProxyClient.discover_session()` — 发现或 spawn 本机 server
- `ping / status / list_logs / stats / shutdown`
- `post_chat` / `stream_chat` — 三协议 endpoint 的 httpx 封装

### ✅ 1.8 前端 UI
- React + Vite + Tailwind + shadcn/ui
- 页面:Dashboard / Chat / Logs
- Chat 页:Protocol 下拉 + Model 选择 + 流式对话(AbortController 取消)
- Dashboard:version / uptime / model / server url

### ✅ 1.9 Tauri 桌面壳
- Tauri 2.x;webview + sidecar PyInstaller exe
- `get_server_url` invoke,UI 起飞时拿 server URL
- single-instance 插件,双击 exe 不开多份

### ✅ 1.10 CI / 打包
- GitHub Actions:ruff check / ruff format --check / pyright / pytest
- `scripts/build.py`:PyInstaller → `dist/chariot.exe` + `dist/chariot-server.exe`
- `scripts/publish.py`:统一发布工具(bump / build / tag)

---

## v1:真实模型接入

### 1.1 模型 config 抽象
- `~/.chariot/config.toml`(或同名 json)指定 model 类型 + 连接参数
- `init_agent()` 读 config 决定 inject 哪个 Model 实现
- 无 config 时回退到 MockModel(现状)

### 1.2 第一个真模型
候选实现(三选一先上):
- `AnthropicAdapterModel`:走 Anthropic Messages API,3 个 protocol 都本地翻成 messages 再调
- `OpenAIAdapterModel`:走 OpenAI Chat Completions API,同理
- `LocalLlamaModel`:llama.cpp / vllm 进程绑 socket,完全本机

### 1.3 model 切换
- UI Dashboard 加"切换 model"下拉 + 重启按钮
- CLI `chariot model list / use <name>`

---

## v2:Agent 进化(chariot 的核心方向)

v0 的 Agent 只做"转发 + 记日志"。v2 开始在这层加真实智能体逻辑。

### 2.1 多轮对话记忆
### 2.2 工具调用 / function calling
### 2.3 自我进化循环
- 读 logs 表里的 feedback,调权重 / 切换 model / 修 prompt
- `agent.evolve()` 定期任务
### 2.4 多 Agent 实例
- logs 加 `agent_id` 字段
- `get_agent(agent_id)` 按 ID 路由
