# Chariot —— 当前状态全景

> 截止 B7 wave 1 + 0.8.9 identity / snapshot 收口(`feat/0.8.9-boundary`,2026-05)。
> 跟 `ARCHITECTURE.md`(早期愿景)的差异:本文只描述**当前真实跑得起来**的代码,
> 不写"将来 X 会怎么做"的设想。后续开发方向单独放在 §10。

---

## 0. 一句话定位

Chariot 是一个**自演化 CLI agent 平台** —— 单进程内可 import `from chariot.agent.run
import AIAgent` 直接用,通过多 surface(CLI / sidecar / desktop)对外暴露能力,
内核负责对话、工具调用、记忆、反思、护栏、技能管理,所有运行时数据落 SQLite
+ disk,**没有外部 server / 集群**。

形态对标 hermes-agent / Cursor agent / Claude Code,而不是 Anthropic Messages
API 的本地代理(0.6.0 之前的形态)。

---

## 1. 三视角架构图

下面三张图分别从 surface 接入、单次 chat 流水线、子系统能力分层 角度看,
不是同一张图的复述。

### 1.1 Surface → 内核(谁能调进来)

```
┌────────────────┐    ┌─────────────────────────┐    ┌────────────┐
│ React UI       │───►│ Tauri shell (Rust)      │───►│ Sidecar    │
│ packages/app   │    │ packages/desktop/tauri  │    │ (stdio     │
└────────────────┘    │  invoke("rpc")          │    │  JSON-RPC) │
                      └─────────────────────────┘    └─────┬──────┘
                                                           │
┌────────────────┐                                         │
│ chariot CLI    │─────────────────────────────────────────┤  (in-process)
│ chariot/cli/   │                                         │
└────────────────┘                                         ▼
                                                  ┌────────────────┐
┌────────────────┐                                │ AIAgent        │
│ Gateways (规划)│────────── 未落 ────────────────►│ chariot/agent/ │
│ telegram/slack │                                │   run.py       │
└────────────────┘                                └───┬────────────┘
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                       Provider    Tool      Skill /
                                                            Guardrail /
                                                            Checkpoint /
                                                            Audit / ...
```

要点:
- **前端不直接调 Python** —— React → Tauri.invoke("rpc") → Rust 转发 stdio → sidecar
- **Tauri 不是业务中心** —— 只是桥,业务逻辑在 sidecar
- **sidecar 跟 CLI 各自起 AIAgent 实例** —— CLI in-process 直调,sidecar 走 JSON-RPC dispatch
- **AIAgent 是所有 surface 的唯一收口** —— 没有"专为 sidecar 写的逻辑"或"专为 CLI 写的逻辑"

### 1.2 单次 chat 流水线(B7 视角)

```
ChatRequest ─► AIAgent.run_chat
                   │
                   │ 1. _apply_profile_reflection(透传 agent_profile.reflection_*)
                   │ 2. _resolve_binding(解析 agent_profile)
                   │ 3. 分支:reflection_enabled? → _run_chat_reflective 循环 N 次
                   │
                   ├─ stateless 路径 ──► _run_stateless_chat
                   │       │ load memory entries / prompt bundle / refs
                   │       │ → _prepare_request → SkillActivator.activate
                   │       │ → @reference expansion
                   │       │ → AgentLoop.stream_chat
                   │       └─ capture_memory(成功) / capture_error_memory(失败)
                   │
                   └─ stateful 路径 ──► _run_stateful_chat
                           │ ConversationLockManager.acquire(进程内 + DB 双层锁)
                           │ persist new user messages
                           │ load history → build full_req → _prepare_request
                           │ ContextCompressor.maybe_compress(长对话)
                           │ prompt_trace 记录
                           │ → AgentLoop.stream_chat
                           │   ├─ provider.generate(stream ChatEvent)
                           │   ├─ buffer assistant blocks
                           │   ├─ if stop_reason=tool_use →
                           │   │   ToolExecutionService.execute_tool_call
                           │   │     ├─ AuditHook.tool_call_pre
                           │   │     ├─ GuardrailEngine.evaluate
                           │   │     │   └─ effective verdict + ApprovalPolicy
                           │   │     ├─ tool.execute(input)
                           │   │     └─ AuditHook.tool_call_post
                           │   └─ build next req → loop
                           └─ persist assistant / capture_memory
```

每个箭头都对应一个具体类。**没有任何"全局函数链"**,所有状态走 AIAgent 实例字段。

### 1.3 子系统能力分层

```
┌─────────────────────────────────────────────────────────────────┐
│ Surface 层 (CLI / sidecar / desktop /gateways)                  │
├─────────────────────────────────────────────────────────────────┤
│ 编排层  AIAgent + AgentLoop                                     │
├──────────────────┬──────────────────┬───────────────────────────┤
│ 运行时基础       │ 控制 / 治理       │ 进化闭环                  │
│                  │                  │                           │
│ providers/       │ guardrails/      │ trace/                    │
│ tools/           │ checkpoints/     │ audit/(事件总线)         │
│ prompt/          │ capability/      │ memory/                   │
│ context/         │                  │ context/(refs+压缩)      │
│ skills/          │                  │ agent/reflection/(critic) │
│ models/(领域类) │                  │ skills/curator+propose    │
│                  │                  │ rl/(B7 wave 1+)          │
├──────────────────┴──────────────────┴───────────────────────────┤
│ 数据层  SQLite(31 张表)+ disk(~/.chariot/{checkpoints,rl})  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 目录布局(顶层每个目录的职责)

```
chariot/
├── agent/        AIAgent / AgentLoop / AgentRegistry / 反思副 LLM / 配置
├── providers/    BaseProvider 抽象 + builtin/(mock, anthropic)
├── tools/        BaseTool 抽象 + builtin/(read_file, list_dir, shell_exec,
│                 http_get, propose_skill)+ ToolExecutionService(护栏接入)
├── skills/       Skill manifest + SkillRegistry + Activator + Propose 服务
│                 + Curator(B6 全套)
├── guardrails/   13 条 builtin 规则 + Engine + ApprovalPolicy + DailyQuotaTracker
├── checkpoints/  CheckpointManager(git stash + sqlite backup + config tarball)
├── audit/        AuditHookManager(7 类自动 hook,事件总线)
├── memory/       MemoryPolicy + MemoryCaptureService + (拼装 prompt 时调)
├── context/      ContextCompressor(长对话压缩)+ ReferenceExpander(@file/@url)
│                 + ContextComposer(snapshot)
├── prompt/       PromptComposer + PromptRepo(bundle / versions / traces)
├── eval/         Eval suite skeleton(测试基建,尚未联线 B7 RL)
├── rl/           B7 wave 1+:TrajectoryExporter + SecretScrubber + (后续 RewardAnnotator / Packager)
├── trace/        TraceWriter(begin_turn / provider_call / tool_call 句柄)
├── repos/        所有表的 DAO(audit / checkpoint / conversation / log /
│                 memory / prompt / skill / task / tool / trace / ...)
├── models/       领域 frozen dataclass(agent_profile / trace / tool / provider)
├── services/     上层组合服务(agent / tool / toolset / trace)
├── database/     session + migrations(001_init + 002_squashed_current + 003..006 identity)
├── rpc/          stdio JSON-RPC 框架(sidecar / ACP / MCP 共用)
├── cli/          typer-based CLI 子命令(每个领域一个 commands/*.py)
└── sidecar/      JSON-RPC method 适配器(methods/*.py)+ __main__ stdio loop
```

```
packages/
├── app/          React + Vite + Tailwind + shadcn/ui(17 个 page)
└── desktop/      Tauri shell(Rust)+ rpc_client 转发到 Python sidecar
```

```
docs/
├── ARCHITECTURE.md     早期愿景(B0-A6 时期写的)
├── OVERVIEW.md         本文档(当前真实状态)
├── DEVELOPMENT.md      当前正在做什么(滞后到用户说"归档"才更新)
├── LONGTERMPLAN.md     长期规划
├── ROADMAP.md          版本里程碑
├── B3-skills-design.md ... B7-rl-design.md  每 milestone 的详细设计
├── guides/             实操指南 + demo (agent-binding-demo.md 是主要 demo doc)
├── history/            按 semver 归档的 DEVELOPMENT.md 快照
└── others/             技术决策 / 实验性散文
```

---

## 3. 平台底座(7 个子系统)

每个子系统下面 4 行格式说明:**入口类 / 主要职责 / 关联表 / 入参核心 invariant**。

### 3.1 agent —— 编排层(AIAgent + AgentLoop)

- **入口**:`chariot.agent.run.AIAgent`(1049 行,内核)+ `chariot.agent.loop.AgentLoop`(297 行)
- **职责**:
  - `AIAgent`:provider 路由 / agent_profile binding 解析 / stateful vs stateless 分流 /
    history 装配 / memory 拣选 / prompt bundle 拼装 / skill 注入 / reference 展开 /
    context 压缩 / 反思-重试主循环
  - `AgentLoop`:provider stream 转发 / assistant blocks buffer / tool 调度 /
    下一轮 request 拼装 / trace handle 写入
- **不做**:`AIAgent` 不知道 tool 怎么跑;`AgentLoop` 不知道 conversation 怎么 lock;
  `BaseProvider` 子类不知道 audit / memory / conversation 任何概念
- **关键 invariant**:
  - Provider 子类**无状态、不碰 DB**(0.6.5 后撤了 per-Provider httpx client,
    走 `ClientCache` 进程级 LRU)
  - `AgentRegistry` per-session(session_key)缓存 AIAgent 实例,LRU 32,
    避免多 surface / 多租户场景重复 init engine + 连接池

### 3.2 providers —— BaseProvider 抽象 + builtin

- **入口**:`BaseProvider` ABC + `ProviderRegistry`(type → 工厂注册)
- **builtin**:`mock`(测试)/ `anthropic`(透传 + SSE 解析)/ `openai`
- **职责**:`generate(ChatRequest) → AsyncIterator[ChatEvent]` 唯一接口;非 Claude
  provider(0.7.0+ openai 等)在子类内部翻译 wire format,**不污染**内核 IR
- **关联表**:`providers`(name → type + options)+ `provider_health`(latency / status / 上次 probe)
- **流契约(详 DESIGN §6.4.2)**:
  - 200 前抛 `ProviderError` → AIAgent 转成 `ChatEvent(kind=error)`
  - 200 后错走 `ChatEvent(error_type=upstream_stream_error)` + return,不抛
  - Provider **不产** `stream_done`(那是 AgentLoop 跨轮收敛职责)

### 3.3 tools —— BaseTool 抽象 + ToolExecutionService

- **入口**:`BaseTool` ABC + `ToolRegistry` + `ToolExecutionService`(护栏接入点)
- **builtin**:`read_file` / `list_dir` / `shell_exec` / `http_get` / `propose_skill`(B6 wave 3)
- **职责契约**:
  - Tool 接 `entry.options` 在 `create()` 时,`execute(input) → tool_result block`
  - **无状态、不碰 DB**(同 Provider)
  - `propose_skill` 例外 —— bootstrap 时注入 `SkillProposeService` 引用(显式标注,
    通过 `attach_service` 方法,不是默认 contract)
- **关联表**:`tools`(seed 5 条 fixture,全部默认 disabled)+ `toolsets`(toolset
  = 命名的 tool 子集,给 agent_profile 引用)
- **接入点**:`ToolExecutionService.execute_tool_call`
  - pre-hook → audit
  - guardrail.evaluate → block / approve
  - tool.execute → tool_result event
  - post-hook → audit(含 duration / is_error)

### 3.4 skills —— B6 全套(loader / registry / activator / propose / curator)

- **入口**:`SkillRegistry`(builtin YAML + DB union)+ `SkillActivator`(注入 system + 过滤 tools)+
  `SkillProposeService`(全链路)+ `SkillCurator`(read-only 4-bucket 分类)
- **数据形态**:`SkillManifest`(schema_version / name / version / prompt / allowed_tools / forbidden_tools)
- **builtin sample**:`code_review` / `debug_helper` / `git_committer`
- **propose 链路**(`propose_skill` tool 触发):
  - guardrail `self_modify_chariot` 拦截 → `enable_self_mod` + yolo 才放行
  - 自动 `CheckpointManager.create("before-skill-propose-<name>")`
  - `SkillRepo.create(enabled=False)` —— 人工 enable 才上线
  - audit 全程留痕
- **关联表**:`skills` + `agent_profiles.default_skill`(v22)

### 3.5 guardrails —— 13 内置规则 + Engine + ApprovalPolicy

- **入口**:`GuardrailEngine.with_defaults(capabilities=...)`
- **13 规则**:
  - shell:`rm_rf` / `chmod_unsafe` / `curl_pipe_sh` / `dd_block_device` / `format_disk` /
    `git_push_force` / `_base`(shell_exec 共享逻辑)
  - file:`write_outside_cwd` / `write_secrets`
  - network:`http_post_unsafe` / `network_exfil`
  - DB:`db_drop_table` / `db_truncate_table`
  - self-mod:`self_modify_chariot`(同时拦 `write_file chariot/` 和 `propose_skill`)
- **决策优先级**:DENY > REQUIRE_APPROVAL > ALLOW
- **配额**:`DailyQuotaTracker` 内存计数;过量自动降级 DENY
- **capability gate**:`enable_self_mod=True` 时把 DENY → REQUIRE_APPROVAL
  (然后 `ApprovalPolicy` 看 yolo 决定放/拒)

### 3.6 checkpoints —— 三件套 snapshot

- **入口**:`CheckpointManager.create(name)` / `rollback(id)` / `delete(id)`
- **三件套**:
  1. `git stash push -u`(非 git 仓库 / 无改动 = no-op 成功)
  2. `sqlite3.Connection.backup()` → `~/.chariot/checkpoints/<name>.sqlite`
  3. config tarball:`~/.chariot/config.yaml` + `.env` → `<name>.tgz`
- **错误处理**:best-effort,每段独立 try/except,失败的段在 `payload.errors` 里报
- **接入点**:`propose_skill` propose 路径自动 create("before-skill-propose-<name>")

### 3.7 audit —— 事件总线(7 类自动 hook)

- **入口**:`AuditHookManager`(单类,持 sessionmaker,best-effort 写入 `audit_events`)
- **事件类型**(`ClassVar` 常量):
  - `tool_call_pre` / `tool_call_post`(ToolExecutionService)
  - `guardrail_verdict`(GuardrailEngine 命中非 ALLOW 时)
  - `memory_store`(MemoryRepo create/update/pin/archive/delete)
  - `checkpoint_create` / `rollback`(CheckpointManager)
  - `skill_store`(SkillRepo create/update/enable/disable/delete)
  - `skill_activate`(SkillActivator.activate 成功后,B6 wave 4)
  - `rl_export`(TrajectoryExporter.export_to_jsonl,B7 wave 1)
- **契约**:异常吞掉,失败不阻断主链路;`sessionmaker=None` 退化 no-op

---

## 4. 进化闭环(6 个子系统)

### 4.1 trace —— 全 turn 数据留痕(B1)

- **入口**:`TraceWriter` ClassVar 单例的 `begin_turn / begin_provider_call / begin_tool_call`
- **关联表**:`trace_turns`(主)+ `trace_provider_calls` + `trace_tool_calls` + `trace_checkpoints`
- **粒度**:**只存摘要**(决策 1)—— 完整 request/response 仍在 `logs` 表(`provider_call.log_id` 关联)
- **数据流出口**:B7 `TrajectoryExporter` 读这几张表 + `messages` + `audit_events`

### 4.2 memory —— 长期记忆 + capture(B3)

- **入口**:`MemoryRepo` + `MemoryCaptureService` + `MemoryPolicy`
- **职责**:
  - 每次 chat turn 跑完后,`capture_memory` 提取关键事实/偏好 → `memories` 表
  - 下一轮 chat 前,`load_memory_entries` 按相关度拣进 prompt
- **关联表**:`memories` + `memory_events`(create/update/pin/archive)+ `memory_links`(跨 conv 引用)

### 4.3 context —— 自动压缩 + @reference 展开(B3)

- **入口**:`ContextCompressor`(长对话用副 LLM 摘要)+ `ReferenceExpander`(`@file:path` / `@url:...`)+ `ContextComposer`(snapshot)
- **关联表**:`context_snapshots` + `context_traces`
- **压缩逻辑**:用 `auxiliary_clients.name='summarizer'` 副 model 压缩;无 summarizer → noop
- **reference 白名单**:`@url:` 走 `http_get` tool 的 `allowed_domains` 集合

### 4.4 reflection / critic —— 反思-重试(B4)

- **入口**:`CriticAgent`(读 `auxiliary_clients.name='critic'`)+ `ReflectionLoop`
- **触发**:`req.reflection_enabled=True` AND critic 已装载 AND `max_retries>0`
- **流程**:跑一轮 → 收 trajectory 喂 critic → `verdict in {PASS, RETRY}` → RETRY
  则把 [REFLECTION] 注入 messages 跑下一轮,记 trace_turn.meta(给 RL 用)
- **关联表**:`auxiliary_clients`(v19)+ `agent_profiles.reflection_enabled`(v20)

### 4.5 skill propose & curator —— 能力固化(B6 wave 3-4)

- **propose 路径**:见 §3.4
- **curator**:`SkillCurator.curate()` 4-bucket(stale / underused / failing / overlapping),
  read-only,**只建议**,不自动改 `skills.enabled`
- **数据源**:`audit_events.skill_activate`(数据基础)+ `skills.prompt`(overlapping 用)

### 4.6 rl —— 数据 pipeline(B7,进行中)

- **当前状态**(wave 1 完成):
  - `TrajectoryExporter`:conversation → list[ExportEntry] → JSONL 文件
  - `SecretScrubber`:正则集合(`sk-` / `AKIA` / `Bearer` / PEM / GitHub PAT)+ `--raw` 兜底
- **数据流**:`messages` + `trace_*` + `audit_events` → ExportEntry → 落 `~/.chariot/rl/trajectories/`
- **后续 wave**(详 docs/B7-rl-design.md):wave 2 RewardAnnotator(静态 + critic 加权)/ wave 3 DatasetPackager + GoldenEvalRunner / wave 4 桌面 /rl 页

---

## 5. Surface 层

### 5.1 CLI(`chariot/cli/`)

- typer + asyncio.run dispatch
- 每个领域一个 `commands/<name>.py`(共 ~24 个),通过 `__main__.py` 统一 `register(app)` 集中注册
- in-process 直调 `AgentRegistry.reserve` 拿 AIAgent 实例
- 全局 flag:`--yolo`(per-process)/ `--quiet`

主要子命令组:
- `chat` / `conversation` / `task` / `job` / `provider` / `tool` / `toolset`
- `agent` / `prompt` / `memory` / `context` / `skill` / `rl`
- `guardrail` / `audit` / `capability` / `checkpoint`
- `auxiliary` / `critic` / `trace` / `eval` / `status` / `logs` / `stats`

### 5.2 Sidecar(`chariot/sidecar/`)

- stdio JSON-RPC server(`chariot/rpc/jsonrpc.py` 框架共享)
- `__main__.py` 跑事件循环;stdin EOF = graceful 退出(不要主动 kill)
- methods 子目录每个领域一个 adapter 类(MethodBase 子类),`register_methods` 集中 wire
- 由 Tauri 主进程 spawn,跟 desktop 1:1

### 5.3 桌面(`packages/app/` + `packages/desktop/tauri/`)

- React + TypeScript + Vite + Tailwind + shadcn/ui
- 路由表 `routes.tsx`:17 个 page(`/dashboard` / `/chat` / `/agents` / `/skills` / `/security` / ...)
- API client `lib/api.ts`:`rpc<T>(method, params)` 透传到 Tauri.invoke
- 错误约定:`RpcError` 携带 sidecar code + message;`SidecarExited` 单独处理

### 5.4 Gateways(规划,0.8.0+ 未落)

- `chariot/gateways/{base.py, registry.py, builtin/{telegram.py, discord.py, ...}}`
- 跟 sidecar / cli 同级,**不**经过 sidecar —— 独立进程 spawn AIAgent
- 当前不存在;只在 ROADMAP / CLAUDE.md 命名规范里提及

---

## 6. 数据层

### 6.1 SQLite(`~/.chariot/chariot.db`)

31 张表,按子系统分组:

| 子系统 | 表 |
|---|---|
| 内核 | `providers` / `tools` / `toolsets` / `toolset_members` / `conversations` / `messages` |
| 平台 | `prompt_bundles` / `prompt_versions` / `prompt_traces` / `context_snapshots` / `context_traces` / `agent_profiles` / `tasks` / `task_runs` / `scheduled_jobs` / `job_runs` |
| 副 LLM | `auxiliary_clients`(critic / summarizer) |
| trace(B1) | `trace_turns` / `trace_provider_calls` / `trace_tool_calls` / `trace_checkpoints` |
| memory(B3) | `memories` / `memory_events` / `memory_links` |
| guardrail(B5) | `audit_events` / `checkpoints` / `capabilities` |
| eval / skill / 监控 | `eval_runs` / `eval_cases` / `skills` / `provider_health` / `logs` |

### 6.2 Migrations(`chariot/database/migrations/`)

当前迁移目录采用“基线 + 增量”结构：

- `001_init.sql`
- `002_squashed_current.sql`
- `003_agent_profile_identity.sql`
- `004_auxiliary_identity.sql`
- `005_toolset_identity.sql`
- `006_job_identity.sql`

当前 `PRAGMA user_version = 6`。

其中 `003` 到 `006` 已完成这轮实体身份治理的核心落地：

- `agent_profiles.id`
- `tasks.agent_profile_id`
- `scheduled_jobs.agent_profile_id`
- `auxiliary_clients.id`
- `toolsets.id`
- `toolset_members.toolset_id`
- `agent_profiles.toolset_id`
- `scheduled_jobs.id`
- `job_runs.job_id`

B7 wave 1 加 `EVENT_RL_EXPORT` 事件类型但**未**改 schema(纯字符串)。

### 6.3 Disk(`~/.chariot/`)

```
~/.chariot/
├── chariot.db              SQLite 主库
├── config.yaml             (可选)用户配置
├── .env                    (可选)secrets
├── checkpoints/            B5 checkpoint 落盘
│   ├── <name>.sqlite       sqlite backup
│   └── <name>.tgz          config tarball
└── rl/                     B7 wave 1+
    └── trajectories/       JSONL 文件
        └── <conv-id>.jsonl
```

---

## 7. 模块依赖图

```
                    ┌──────────────┐
                    │ AIAgent      │ ──┐ (orchestrator)
                    └──────┬───────┘   │
                           │           │
        ┌──────────────────┼───────────┴────────────────┐
        ▼                  ▼                            ▼
   ┌─────────┐      ┌──────────┐               ┌────────────────┐
   │ Provider│      │ AgentLoop│               │ MemoryPolicy /  │
   │ (mock / │      │          │               │ Capture        │
   │ anthr.) │      └────┬─────┘               └────────────────┘
   └─────────┘           │                              ▲
                         ▼                              │
                   ┌──────────────────────┐             │
                   │ ToolExecutionService │             │
                   └─────┬─────────┬──────┘             │
                         │         │                    │
                ┌────────▼─┐    ┌──▼───────────┐        │
                │ Tools    │    │ Guardrail    │        │
                │ (5 内置) │    │ + Approval   │        │
                └────┬─────┘    └──────┬───────┘        │
                     │                 │                │
                     │            ┌────▼──────────┐     │
                     │            │ Capabilities  │     │
                     │            └───────────────┘     │
                     │                                  │
                     └─────────┐                        │
                               ▼                        │
                  ┌────────────────────────────┐        │
                  │ AuditHookManager(总线)    │◄───────┘
                  └────────┬───────────────────┘
                           ▼
                     ┌──────────┐
                     │ DB Repos │ (audit / memory / skill / checkpoint / ...)
                     └────┬─────┘
                          ▼
                      SQLite
```

- **零循环依赖**(import 拓扑可静态验证):
  - `agent` 依赖 `providers / tools / skills / guardrails / checkpoints / audit / memory / context / prompt / repos / trace`
  - `tools/execution` 依赖 `guardrails / audit`(不依赖 agent)
  - `skills/propose_service` 依赖 `checkpoints / audit / repos`(不依赖 agent)
  - 反向引用全走 `TYPE_CHECKING` import + 字符串注解

---

## 8. 一次完整 chat 端到端时序(B7 视角)

stateful + agent_profile + reflection + skill 全开的场景:

```
1. CLI/sidecar 接到请求 → 构造 ChatRequest(conversation_id="cv-1", agent_profile="reviewer", skill="code_review", reflection_enabled=True)

2. AgentRegistry.reserve("cv-1") → 拿到/新建 AIAgent 实例(共享 engine + ClientCache)

3. AIAgent.run_chat(req)
   ├─ _apply_profile_reflection:profile.reflection_enabled → 覆盖 req.reflection_*
   └─ branch: reflection_enabled=True → _run_chat_reflective 循环

4. iteration 0:
   ├─ _run_chat_once → _resolve_binding(reviewer profile)→ provider 路由(profile.provider_id / req.provider_ref)
   ├─ trace.begin_turn(写 trace_turns 一行,status=running)
   ├─ _run_stateful_chat
   │  ├─ ConversationLockManager.acquire("cv-1") 双层锁
   │  ├─ ConversationRepo.ensure_exists + persist user message
   │  ├─ load_history → 拼 full_req
   │  ├─ load_memory_entries(MemoryPolicy 拣)
   │  ├─ ContextCompressor.maybe_compress(超阈值才跑)
   │  ├─ _prepare_request:
   │  │   ├─ PromptComposer.render_layers_text(bundle + memory + history)
   │  │   └─ SkillActivator.activate(code_review) → 注入 <skill> 块 + tool filter
   │  │       └─ AuditHook.record_skill_activate
   │  ├─ ReferenceExpander.expand(@file / @url 解析)
   │  ├─ record prompt_trace + context_trace
   │  └─ AgentLoop.stream_chat(full_req)
   │     ├─ provider.generate → yield ChatEvent 流
   │     │  └─ trace.begin_provider_call → finalize_provider_call
   │     ├─ buffer assistant blocks(text + tool_use)
   │     ├─ stop_reason=tool_use → ToolExecutionService.execute_tool_call
   │     │  ├─ AuditHook.tool_call_pre
   │     │  ├─ GuardrailEngine.evaluate → 命中 verdict
   │     │  │  ├─ DENY → 直接 tool_result is_error + AuditHook.guardrail_verdict
   │     │  │  └─ REQUIRE_APPROVAL → ApprovalPolicy.auto_approve(yolo?)
   │     │  ├─ tool.execute(input) → tool_result block
   │     │  └─ AuditHook.tool_call_post
   │     ├─ persist tool_result message
   │     └─ 下一轮 → 直到 stop_reason=end_turn
   ├─ trace.finalize_turn(status=completed)
   └─ capture_memory(把这次交互产的"事实/偏好"写 memories 表)

5. reflection step:
   ├─ ReflectionLoop.step(buffer, critic_agent)
   ├─ critic 喂 trajectory → verdict ∈ {PASS, RETRY}
   ├─ RETRY → 注入 [REFLECTION] 块到 messages → iteration 1 重跑
   └─ PASS / 超 max_retries → return

6. 后台(异步):
   ├─ B7 用户跑 chariot rl export "cv-1"
   ├─ TrajectoryExporter.export("cv-1")
   │  ├─ 读 trace_turns(每轮一条)
   │  ├─ 关联 messages(按时间窗切 prompt/response)
   │  ├─ 关联 audit_events(guardrail_hits / skill_activations / reflection_records)
   │  └─ 走 SecretScrubber → 落 ~/.chariot/rl/trajectories/cv-1.jsonl
   └─ AuditHook.record_rl_export
```

---

## 9. 关键 invariant(不能踩破的边界)

> 详见 `CLAUDE.md`(协作约定)。下面是最经常被踩的:

1. **Provider / Tool 子类无状态、不碰 DB**;持久化由 AIAgent 通过 LogWriter / ConvoRepo 收口
2. **`AIAgent.run_chat` 是 `AsyncIterator[ChatEvent]`** —— 200 后不抛异常,改 yield error event 再 return
3. **Provider 不产 `stream_done`** —— AgentLoop 跨轮收敛
4. **双层锁**:同 convo_id 同进程内串行(asyncio.Lock dict)+ 跨进程 SQLite `BEGIN IMMEDIATE` 短事务
5. **错误字段是 `error_type` / `error_message`**,**不是** `code` / `message`
6. **AuditHook 失败吞掉,不阻断主链路**(best-effort 契约)
7. **Checkpoint create/rollback 三段独立 try/except**,失败的段在 payload.errors 里报
8. **skill propose 默认 enabled=False** —— 人工手动 enable 才生效
9. **secret_scrubber 默认开,`--raw` 兜底**(打 banner 警告)
10. **sidecar stdin 关闭 = graceful 退出**,不要主动 kill

---

## 10. 后续开发方向

### 10.1 短期(B7 进行中)

- **wave 2 RewardAnnotator**:`StaticReward`(reflection / guardrail / tool error
  ratio / stop_reason 等)+ `CriticReward`(B4 critic agent 1-10 → [-1, +1])加权
- **wave 3 DatasetPackager + GoldenEval**:dedupe / train-eval split / trl-sft 格式 + 3 条 smoke goal
- **wave 4 桌面 `/rl` 页 + demo doc §7.18 + LONGTERMPLAN.md §17 占位**

### 10.2 中期(B8 真正 train —— 接外部 trainer)

- 不内嵌训练循环;chariot 只产数据
- 对接 trl `SFTTrainer` / verl / unsloth 等外部框架
- 桌面 `/rl/train` 入口:启动 background subprocess 跑外部 trainer,watch stdout 进度
- 训练产物(LoRA adapter / fine-tuned weights)落 `~/.chariot/rl/checkpoints/`,
  跟 chariot 自己的 checkpoint(B5)分目录

### 10.3 中期(0.8.0+ Gateways)

- `chariot/gateways/{base.py, registry.py, builtin/{telegram, discord, slack}}`
- 每个 gateway 独立进程 spawn AIAgent;跟 sidecar 同级 surface
- 接 webhook → ChatRequest → 走 AIAgent
- 多租户:`session_key="gateway:telegram:<chat_id>"` 共享 AgentRegistry

### 10.4 长期(0.11.0+ 第三方插件)

- `chariot/tools/external/` / `chariot/skills/external/` 子目录装第三方 pip 包
- 启动时 entry_points 扫描自动 register
- Skill 走 GitHub-hosted YAML repo(类似 helm chart repo)
- Provider 接 vllm / llama.cpp 本地 model

### 10.5 长期(自演化闭环 closing)

按 ARCHITECTURE.md §7 的设想:

```
trace ─► reflection ─► update ─► evaluation ─► governance ─► trace
```

当前状态:
- ✅ trace(B1)
- ✅ reflection(B4)
- 🟡 update:skill propose(B6)是局部 update;后续需扩到 prompt bundle / tool config
- 🟡 evaluation:`chariot eval` skeleton 在,但没接 B7 RL pipeline
- ✅ governance(B5 全套)

闭环成立时(`ARCHITECTURE.md` §9):
1. agent 通过 reflection 形成改进建议 ✅
2. 建议形成 skill 或受控 update ✅
3. update 通过 eval 判断"真的更好" 🔴(B7 wave 3 才接)
4. 整过程受 governance 控制 ✅
5. 可 checkpoint / rollback ✅

**当前最近的一公里**:把 B7 RL pipeline 跑通(wave 2-4),让 dataset 真正流到外部
trainer,跑出 fine-tuned weights 灌回 chariot Provider,把 §10.5 这条闭环走通一次
end-to-end demo。

---

## 11. 怎么继续读

- 想看某个 milestone 的细节 → `docs/B<n>-*-design.md`
- 想看实操 demo / 验证步骤 → `docs/guides/agent-binding-demo.md`(§7.1 - §7.17 累计 17 个 wave 的 demo)
- 想看历史版本计划 → `docs/history/<semver>/DEVELOPMENT.md`
- 想看长期路线(跨 milestone)→ `docs/LONGTERMPLAN.md`
- 想看版本里程碑状态 → `docs/ROADMAP.md`
- 想加入协作 → `CLAUDE.md`(协作约定 / 命名规范 / git 规则)
