# Chariot 路线图

0.1.0 ~ 0.5.0 已落地的范围见 `docs/history/` 各版本快照。本文档记录
**未来方向**(0.6.0 起),跨版本沿用,不归档。

> **0.6.0 起定位扭转**:chariot 从"本机 server(对外暴露 Anthropic Messages
> 协议)"扭转为"**自演化 CLI agent**(对标 hermes-agent),跑在 VPS / 本地 / 云,
> 多个 surface(CLI / Sidecar / Gateways / ACP / MCP)各自启进程内
> `from chariot.agent.run import AIAgent` 直接构造,共享
> `~/.chariot/chariot.db`"。详见 [`DESIGN.md`](DESIGN.md)。
>
> **0.6.0 起目录约定**:
> - 顶层平铺功能模块(`agent/` 狭义内核 / `tools/` / `providers/` / `repos/` /
>   `database/` / `rpc/`)
> - Surface 各自独立目录(`cli/` / `sidecar/`,后续加 `gateways/` / `acp/` / `mcp/`)
> - 装多个同类可插拔实现的目录套 `builtin/`(后续 `external/` 给第三方插件)

---

## v1:真实模型接入 / 选型 ✅(0.2.0 ~ 0.3.1 完成)

- ✅ **Model 配置体系**:DB-backed `models` 表 + `params` 列承载 sampling 默认值
- ✅ **AnthropicModel**:透传到 Anthropic Messages API,错误映射 + 流式 SSE
  (0.6.0 重构为 `AnthropicProvider`,SSE 解析后产 ChatEvent;不再字节透传;
  位置 `chariot/providers/builtin/anthropic.py`)
- ✅ **Model 路由**:client 在 body.model 写 entry name 直接路由
  (0.6.0 起 `body` 概念让位给 `ChatRequest.model`(跟 Claude API 1:1),
  语义不变)

## v2:AIAgent 进化(chariot 的核心方向)

- ✅ **多轮对话记忆**(0.4.0)—— `conversations` + `messages` 表;
  `X-Chariot-Conversation` header 触发 stateful;Agent.handle 接 ConversationRepo
- ✅ **工具调用 / function calling**(0.4.0)—— BaseTool + ToolRegistry + 4 内置
  工具(read_file / list_dir / shell_exec / http_get);Agent slow path 工具循环
- ✅ **协议级流式工具循环**(0.5.0)—— Agent 全程 streaming;`tool_use` /
  `tool_result` 实时出现在 SSE;CLI / UI / 透传客户端共享一份原生事件流
- **架构定位扭转 + 库化 + 目录重组**(0.6.0,开发中)—— 撤 `chariot/server/` +
  fastapi;`AIAgent` 改可 import 库;协议无关 `ChatRequest` / `ChatEvent` 内核;
  `BaseProvider` 抽象替代 `Model`;`AgentLoop` 替代 `ToolLoop`;
  `ChatRequest` / `ChatEvent` / `messages.content` 三层跟 Claude API 1:1;
  Tauri 通过 stdio
  JSON-RPC sidecar 对接(`chariot/sidecar/`);RPC 框架共享(`chariot/rpc/`);
  顶层目录全面重组(详见 DESIGN.md)
  - **中断机制(cancel)**——延到 sidecar 阶段(S.7~S.9)统一搞:
    `AIAgent` 暴露 `cancel(run_id)` / 内部 `asyncio.Event`;`AgentLoop`
    在每轮起头 / event 间 / tool 执行前三处 check;被 cancel → yield
    `ChatEvent(kind=error, error_type=cancelled)` + return;stateful 模式
    把已流出来的 assistant text 落库 + 标 `stop_reason=cancelled`(避免
    下轮 load history 拿到半截)。Surface 接入:CLI 加 SIGINT handler;
    sidecar 加 `chat.cancel` JSON-RPC 方法;tool 子类(尤其 shell_exec)
    在 `try/finally` 里 kill subprocess。0.6.0 内核阶段不做(S.6 已收尾)
- **OpenAIProvider + Memory + Skills**(0.7.0)—— 自演化的两大数据底座 + 多 Provider:
  - `chariot/providers/builtin/openai.py`:覆盖 OpenAI 兼容协议生态(直接
    OpenAI / Azure / OpenRouter / Kimi / DeepSeek / z.ai / 通义 / Xiaomi /
    NVIDIA NIM 等)
  - `memory_facts` 表:agent 在对话中提取的 user facts(对标 hermes Honcho 思路);
    新会话起步时 prepend 到 system prompt
  - `skills` 表 + `BaseSkill` ABC:procedural memory,agent 自创建 / 改进 skill;
    skill 是参数化的"程序化经验"(对标 hermes skills system / agentskills.io 标准)
  - AIAgent 加 nudge 机制:复杂任务收尾时主动建议"要不要把这个流程沉淀成 skill?"
- **Gateways(IM 平台接入)+ 自我进化循环**(0.8.0)—— `chariot/gateways/` +
  平台 adapter:
  - `gateways/builtin/telegram.py` / `discord.py`(社区最大)
  - `chariot gateway start --platform <name>` 长 live 进程,接 IM 平台 push,
    转 ChatRequest 跑 AIAgent,把 ChatEvent 编成平台消息发回
  - `agent.evolve()` 定期任务:读 `logs` / `messages` 历史 + 用户隐式反馈
    (重试 / 撤回 / 表扬),调权重(prefer 哪种工具 / 哪个模型)/ 修 system
    prompt / 沉淀 / 淘汰 skills
- **Cron 调度 + 多 AIAgent 实例 + LocalLlamaProvider**(0.9.0)—— 三件套:
  - `cron_jobs` 表 + `Scheduler` 类 + 自然语言时间解析;到点把 prompt 喂
    AIAgent,结果通过指定 surface 投递(对标 hermes cron)
  - `agent_id` 维度全表加;`get_agent(agent_id)` 按 ID 路由;config 支持多
    agent profile(每个有自己的 system prompt / tools 白名单 / memory 隔离)
  - `chariot/providers/builtin/local_llama.py`:本地 llama.cpp / vllm / Ollama 后端
- **ACP / MCP 接入**(0.10.0+)—— 多 surface 扩展:
  - `chariot/acp/`:ACP server(Zed / VSCode / JetBrains 等 IDE 接入)
  - `chariot/mcp/`:MCP server(暴露 chariot 工具给别的 LLM)/ MCP client
    (用别人的 MCP server 当工具源)
  - 共享 `chariot/rpc/jsonrpc.py` 框架(stdio JSON-RPC)
- **Subagent 派生**(0.10.0+)—— AIAgent 主流程能 spawn 隔离 subagent 并行处理
  独立任务,共享父 agent 的 memory / skills 但独占 conversation
- **MultiProvider 路由策略**(0.10.0+)—— `ChatRequest.model` 之外加策略:
  cheap / fast / smart 等 alias,自动 fallback
- **Plugins 系统**(0.11.0+,推迟到生态有 ≥3 个独立 Provider / Tool / Skill /
  Gateway-platform 实现后再上,避免空抽象)—— 路径:
  - `chariot/tools/external/` —— 第三方 / 用户在 `~/.chariot/tools/` 安装的
  - `chariot/providers/external/`
  - `chariot/gateways/external/`

## v3:UI 升级

- ✅ **Chat 会话持久化**(0.4.0)—— GUI 侧栏列表 / 选择 / rename / delete
- **TUI 升级**(0.10.0)—— 现 CLI REPL → 完整 TUI(候选:Textual / Ink-Python /
  自研);多行编辑、slash 自动补全、流式工具卡片
- **Chat 原始请求 / 响应预览面板** —— Chat 页可折叠 JSON 面板,显示当前 turn 的
  ChatRequest + 各轮 ChatEvent 序列。协议调试用
- **会话导出 / 导入** —— `chariot conversation export <id>`;多机协作场景把
  对话从笔记本同步到 VPS

## v4:管理面体验

- 实时日志流(sidecar 直接 push,撤旧 SSE / WebSocket polling)
- 按 model / Provider / agent_id 切分的用量统计(时间序列图)
- 配置导入导出(`chariot model export/import` / `tool export/import` —— DB
  entries 序列化为 JSON,跨机迁移 / 版本控制友好)

## v5:发版与分发

- **自动更新真实启用**(0.5.0 已合 tauri-plugin-updater,但 pubkey 占位未替换)
- **代码签名**(Authenticode / Apple Developer 证书)
- 跨平台打包(macOS / Linux);`chariot-sidecar.spec` 0.6.0 起需在三平台验证
- 首个 Release 闭环(tag → CI → 签名 installer → latest.json → updater)

## v6:运维 / 扩展

- 请求日志 TTL 清理策略(`logs` 表;`memory_facts` / `skills` 表也加 TTL?待定)
- **多机协作模式**(替代旧"多用户账户")—— 库化后没有"中心 server",多机协作
  靠两条路:
  - 同步 `~/.chariot/chariot.db`(rclone / syncthing / git-annex)
  - SSH 进 VPS 跑 `chariot chat` / `chariot gateway start`
- 多语言(i18n)

## 主动跳过(确认不做)

- **批量 / RL 训练环境**(hermes Atropos)—— Nous 给自家研究用,chariot 用户
  场景不需要;轻量批量(`chariot batch run --jobs jobs.yaml`)可在 0.11.0+ 看
  需求加
- **多终端后端 daytona / modal / singularity**(hermes environments)—— 个人
  用户 overkill;`shell_exec` 工具最多扩到 local + docker + ssh 三种
- **小众 IM 平台**(bluebubbles / mattermost / dingtalk / weixin / qqbot 等)——
  维护成本不划算;0.11.0+ Plugins 系统上线后,社区可在 `gateways/external/`
  自行贡献
