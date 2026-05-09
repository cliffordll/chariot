# Chariot Architecture

> 本文描述 `chariot` 当前的真实结构，以及后续平台化时应该如何扩展。
> 重点不是泛泛讲“要做平台”，而是把三件事说清楚：
>
> 1. 现在代码是怎么跑的。
> 2. 哪些边界应该保留。
> 3. 后续新能力应该挂到哪里。

## 提纲

如果只想先抓重点，先看这一节。

### 这份文档的核心结论

1. `chariot` 现有主链路是成立的，不需要推倒重来。
2. 当前最重要的不是改目录结构，而是把平台子系统显式长出来。
3. `AIAgent` 和 `AgentLoop` 要继续保留两层分工。
4. 后续设计要分成两层看：
   - 平台底座：`runtime（运行时）`、`prompt（提示词系统）`、`context（上下文管理）`、`memory（长期记忆）`、`tool management（工具管理）`、`provider management（模型提供方管理）`、`agent/task management（智能体与任务管理）`、`artifact management（结果产物管理）`
   - 进化闭环：`trace（执行轨迹） -> reflection（反思） -> update（更新） -> evaluation（评估） -> governance（治理）`

### 这份文档怎么读

- 想先看当前系统怎么跑：看 `4. 当前主链路`
- 想先看哪些边界不能乱动：看 `5. 核心边界`
- 想先看后面到底要补哪些能力：看 `6. 平台主线能力`
  这里会分别解释 `runtime（运行时）`、`prompt（提示词系统）`、`context（上下文管理）`、`memory（长期记忆）` 等模块
- 想先看自主进化怎么闭环：看 `7. 自主进化闭环`
  这里会解释 `trace（执行轨迹）`、`reflection（反思）`、`update（更新）`、`evaluation（评估）`、`governance（治理）`
- 想先看当前最紧迫的问题：看 `8. 当前最需要补强的结构问题`

## 1. 当前判断

当前 `chariot` 已经有一条成立的主链路：

`React UI -> Tauri invoke("rpc") -> Rust rpc_client -> Python sidecar -> AIAgent -> Provider / Tool / SQLite`

这意味着：

- 现在不是“需要推倒重来”的项目。
- 当前问题主要不是“完全没有能力”，而是“平台层能力还没有明确长出来”。
- 后续设计重点应该是补齐平台底座和进化闭环，而不是改成另一套目录结构。

## 2. 当前仓库结构

当前主要目录职责如下：

- `chariot/agent/`
  - agent runtime 核心
  - 包括 `AIAgent`、`AgentLoop`、`AgentRegistry`
- `chariot/providers/`
  - provider 抽象、provider registry、provider runtime
- `chariot/tools/`
  - tool 抽象、tool registry、builtin tools
- `chariot/sidecar/`
  - Python sidecar
  - 通过 stdio JSON-RPC 暴露给 Tauri
- `chariot/cli/`
  - CLI entry 和各子命令
- `chariot/database/`
  - SQLite session、ORM、migrations
- `chariot/repos/`
  - provider / tool / conversation / log 等数据访问层
- `packages/app/`
  - React 前端
- `packages/desktop/tauri/`
  - Rust / Tauri desktop shell

当前目录结构本身没有大问题，后续建议继续保持。

## 3. 三张架构图

这里不用一张“大而全”的图，而是拆成三张。

### 3.1 接入图

这张图只回答“请求怎么进入系统”。

```text
packages/app/                        packages/desktop/tauri/
React UI                             Tauri shell + Rust rpc client
    |                                            |
    | api.ts                                     | invoke("rpc")
    v                                            v
+------------------------------------------------------------+
|                Python sidecar (stdio JSON-RPC)             |
|                    chariot/sidecar/                        |
|                                                            |
|  methods/chat.py  methods/provider.py  methods/tool.py     |
|  methods/conversation.py methods/log.py                           |
+-------------------------------|----------------------------+
                                |
                                v
                      chariot/agent/AIAgent

CLI path:

chariot/cli/ ---------------------------------> chariot/agent/AIAgent
                in-process call
```

重点：

- 前端不是直接调 Python。
- Tauri 不是业务中心，只是桥。
- sidecar 是桌面端入口。
- CLI 和 sidecar 最终都会收敛到 `AIAgent`。

### 3.2 Runtime 执行图

这张图只回答“一次 chat request 在核心代码里怎么跑完”。

```text
ChatRequest
    |
    v
AIAgent
    |
    | 1. resolve provider
    | 2. inject default tools
    | 3. branch: stateful / stateless
    |
    +--------------------- stateless ----------------------+
    |                                                      |
    v                                                      |
AgentLoop                                                  |
    |                                                      |
    | provider.generate(req)                               |
    | -> stream ChatEvent                                  |
    | -> collect assistant blocks                          |
    | -> execute tools if stop_reason=tool_use             |
    | -> build next request                                |
    | -> finish                                            |
    |                                                      |
    +------------------------------------------------------+
    |
    +---------------------- stateful ----------------------+
                                                           |
                                                           v
                                          ConversationLockManager.acquire(conversation_id)
                                                           |
                                                           v
                                                 ConversationRepo.ensure_exists(...)
                                                           |
                                                           v
                                           persist new user messages to DB
                                                           |
                                                           v
                                           load history from messages table
                                                           |
                                                           v
                                          build full request with history
                                                           |
                                                           v
                                                      AgentLoop
                                                           |
                                                           v
                                        persist assistant messages / tool results
```

重点：

- `AIAgent` 负责外层 orchestration（编排），不是整个 tool loop。
- `AgentLoop` 负责 provider stream 和 tool loop。
- `stateful` 比 `stateless` 多出 lock、history、persistence。

### 3.3 平台扩展图

这张图只回答“后续平台能力往哪里挂”，不再混入当前请求链路细节。

```text
                        chariot/
                           |
      +--------------------+--------------------+
      |                    |                    |
    agent              providers              tools
      |                    |                    |
      +---------- current runtime skeleton -----+
                           |
      +--------------------+--------------------+
      |                    |                    |
    prompt              context               memory
      |                    |                    |
      +------------ runtime context layer ------+
                           |
      +--------------------+--------------------+
      |                    |                    |
 tool_mgmt           provider_mgmt         agent_mgmt
      |                    |                    |
      +------------- platform control layer ----+
                           |
      +--------------------+--------------------+
      |                    |                    |
    skills               eval                 audit
      |                    |                    |
      +------------- evolution support layer ---+
                           |
      +--------------------+--------------------+
      |                    |                    |
 checkpoints          delegation           artifact_mgmt
                           |
                      guardrails
                           |
                         cron
```

重点：

- 新能力不应该继续堆到 `agent/loop.py`。
- 应该作为 `chariot/` 下的独立子系统长出来。
- 当前 runtime skeleton 仍然是 `agent / providers / tools`。
- 后续平台能力需要明确分层，而不是都叫“future platform”。

## 4. 当前主链路

### 4.1 Chat request 路径

一次 chat request 当前的运行路径是：

1. 前端调用 `packages/app/src/lib/api.ts`
2. Tauri `invoke("rpc")`
3. Rust `rpc_client.rs` 把请求写给 Python sidecar
4. `chariot/sidecar/methods/chat.py` 解析 RPC 参数
5. `AIAgent` 选择 provider、准备 request
6. `AgentLoop` 消费 provider stream，执行 tool loop
7. 会话和消息通过 `ConversationRepo` 落到 SQLite

### 4.2 Stateful request 路径

有 `conversation_id` 时，当前还会多经过这些步骤：

1. `ConversationLockManager` 获取本地会话锁
2. `ConversationRepo.ensure_exists(...)`
3. 持久化本轮新 user message
4. 从 `messages` 表加载历史
5. 拼出完整 request 再进入 `AgentLoop`

这说明当前系统已经默认支持：

- stateless chat
- stateful chat
- tool loop
- DB persistence

后续平台化是在现有 runtime 上补层，而不是从零开始。

## 5. 核心边界

### 5.1 保留 `AIAgent` / `AgentLoop` 两层

这两层不应该合并。

建议边界如下：

- `AIAgent`
  - 外层 orchestration
  - provider routing
  - stateful / stateless 分流
  - history 装配
  - prompt / memory / skill / guardrail 注入点
- `AgentLoop`
  - 内层 chat / tool execution loop
  - 消费 provider stream
  - 重组 assistant blocks
  - 执行 tool calls
  - 生成下一轮 request

也就是：

- `AIAgent` 解决“这次请求怎么准备好”
- `AgentLoop` 解决“准备好之后怎么跑完”

### 5.2 保留当前顶层目录风格

当前不引入：

- `core/`
- `platform/`
- `surfaces/`

后续新子系统直接加在 `chariot/` 下，例如：

- `chariot/prompt/`
- `chariot/context/`
- `chariot/memory/`
- `chariot/tool_mgmt/`
- `chariot/provider_mgmt/`
- `chariot/agent_mgmt/`
- `chariot/artifact_mgmt/`
- `chariot/skills/`
- `chariot/eval/`
- `chariot/audit/`
- `chariot/checkpoints/`
- `chariot/delegation/`
- `chariot/guardrails/`

原因很简单：

- 当前目录层次已经够清楚。
- 再加一层只会拉长路径，不会增加结构价值。

### 5.3 保留“DB 存元数据，文件系统存大对象”的方向

数据库继续存：

- providers
- tools
- conversations
- messages
- logs
- memories
- traces
- audit_events
- eval_runs
- skills 索引
- checkpoints 索引

文件系统后续存：

- skill files
- prompt bundles
- checkpoint snapshots
- eval artifacts
- generated tool source
- exported datasets

这个边界后面不要反复摇摆。

## 6. 平台主线能力

后续整体设计不能只看“自主进化闭环”，还要先看平台底座。

### 6.1 Runtime

负责：

- chat request 执行
- stateful / stateless 分流
- provider stream 消费
- tool loop
- session persistence

当前主要落点：

- `chariot/agent/`
- `chariot/providers/`
- `chariot/tools/`
- `chariot/repos/`

### 6.2 Prompt system

这不是简单的 system prompt 拼接，而是完整 prompt 组装系统。

应包含：

- base system prompt
- developer prompt
- runtime prompt layers
- memory injection
- skill prompt injection
- tool schema / tool instruction injection
- prompt compaction
- prompt versioning
- prompt trace

建议模块：

- `chariot/prompt/`

### 6.3 Context management

这层负责“当前 turn 最终拿到哪些上下文”，不应和 memory 混为一谈。

应包含：

- history trimming
- context selection
- context ranking
- context compression
- artifact reference
- cross-session context reuse

建议模块：

- `chariot/context/`

### 6.4 Memory system

这层负责长期记忆，不只是 CRUD。

应包含：

- user memory
- workspace memory
- project memory
- instruction memory
- lesson memory
- retrieval policy
- injection policy
- consolidation
- archive / decay

建议模块：

- `chariot/memory/`

### 6.5 Tool management

这层不能只停留在 `ToolRegistry`。

应包含：

- tool registry
- tool config
- tool enable / disable
- tool policy
- tool approval
- tool audit
- tool version
- toolset / tool profile
- generated tool lifecycle

建议模块：

- `chariot/tool_mgmt/`
- `chariot/tools/`

### 6.6 Provider management

这层不能只停留在 provider contract。

应包含：

- provider profile
- model routing
- capability matrix
- retry policy
- fallback policy
- cost tracking
- health status
- runtime reload

建议模块：

- `chariot/provider_mgmt/`
- `chariot/providers/`

### 6.7 Agent / task management

后续有 delegation、多角色、后台 agent 时，这层必须独立。

应包含：

- agent profile
- role presets
- session policy
- parent / child lineage
- background agent lifecycle
- task object
- task state
- resumable execution
- scheduled jobs

建议模块：

- `chariot/agent_mgmt/`
- `chariot/delegation/`
- `chariot/cron/`

### 6.8 Artifact management

自主进化一定会产生很多中间产物，不能全散在日志里。

应包含：

- trace artifacts
- eval reports
- skill proposals
- checkpoint bundles
- generated files
- dataset exports

建议模块：

- `chariot/artifact_mgmt/`

### 6.9 Skills

这层负责把经验固化成可复用能力。

应包含：

- skill packaging
- skill registry
- skill activation
- skill usage tracking
- skill proposal
- skill update / rollback / archive

建议模块：

- `chariot/skills/`

### 6.10 Evaluation

这层负责判断“变化是否带来提升”。

应包含：

- eval cases
- eval suites
- baseline compare
- regression detection
- artifact export
- dataset packaging

建议模块：

- `chariot/eval/`

### 6.11 Governance

这层负责把自主进化限制在可控边界内。

应包含：

- guardrails
- approval
- checkpoint / rollback
- quota / budget
- policy engine
- audit

建议模块：

- `chariot/guardrails/`
- `chariot/checkpoints/`
- `chariot/audit/`

## 7. 自主进化闭环

平台底座之上，真正的自主进化闭环是：

`trace（执行轨迹） -> reflection（反思） -> update（更新） -> evaluation（评估） -> governance（治理）`

### 7.1 Trace

记录发生了什么：

- turn trace
- provider trace
- tool trace
- checkpoint trace

### 7.2 Reflection

从 trace 和 memory 中总结：

- 本轮哪里失败
- 哪些模式重复出现
- 哪个 tool 用得不对
- 是否该形成 skill 或 policy 变更

### 7.3 Update

把反思转成真正更新：

- 新 skill
- skill patch
- tool profile 变更
- prompt bundle 更新
- generated tool proposal

### 7.4 Evaluation

判断更新是否有效：

- baseline compare
- regression detection
- cost / latency 对比
- task success 对比

### 7.5 Governance

确保更新不会失控：

- policy review
- approval
- checkpoint
- rollback
- audit trail

## 8. 当前最需要补强的结构问题

### 8.1 主循环和 provider contract 不够稳

当前 `AgentLoop` 过于依赖 provider 原始事件顺序。

需要补：

- request normalization
- provider capabilities
- provider event normalizer / validator
- clearer provider error model

### 8.2 Runtime 与 sidecar 分层不够清楚

当前 sidecar methods 逐渐承担：

- 参数解析
- 业务编排
- repo 调用
- 错误映射

需要补：

- request decoder
- application service
- RPC adapter

### 8.3 Tool execution 只有执行，没有完整治理

当前 tool system 重点还在 registry 和 dispatch。

需要补：

- policy check
- approval hook
- execution audit
- tool profile
- generated tool lifecycle

### 8.4 平台子系统还没有显式长出来

现在讨论中常提到：

- prompt
- context
- memory
- tool management
- provider management
- agent management
- artifact management

但这些在代码结构里还没有成为独立模块。

这也是后续设计文档必须明确补出来的原因。

## 9. 当前结论

`chariot` 现在最重要的不是再做一轮目录级大重构，而是：

1. 保住现有主链路。
2. 收紧 `AIAgent` / `AgentLoop` / provider / sidecar 的边界。
3. 把平台主线能力一条条显式长出来。
4. 在这些平台能力之上，再做自主进化闭环。

如果只用一句话概括：

`chariot` 后续要做的，不是把一个 chat app 改名叫平台，而是把 runtime、prompt、context、memory、tool management、provider management、agent management、artifact management、skills、evaluation、governance 真正补齐。
