# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史版本见 `docs/history/<version>/DEVELOPMENT.md`。
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不得改写内容或丢失信息。

## 当前阶段

当前只推进 `Milestone A2: Context management`。

目标是把会话上下文从“隐式混在 request 里”收口成一条可检查、可追踪、可裁剪、可复现的上下文链路，让一次 turn 里真正进入 prompt 的上下文来源、范围和版本都能稳定复现。

### 本阶段交付

- `context snapshot`：记录一次 turn 可用的上下文快照。
- `context slice`：把上下文按来源和用途切成稳定片段。
- `context version`：记录上下文装配规则或快照版本。
- `context trace`：记录这次 turn 最终用了哪些上下文、怎么裁剪、怎么注入。
- 最小管理入口：CLI 和 sidecar 可以查看某个 conversation 的上下文快照和 trace。

### 工作原理

这一阶段不是先做一个更复杂的“上下文生成器”，而是先把上下文收口成可观察边界。

- `AIAgent` 仍然负责 turn 调度和 provider 调用。
- `context system` 负责决定“这次 turn 可见哪些上下文、上下文按什么顺序进入 prompt、哪些被裁掉”。
- `prompt system` 负责把 context 产出的结果继续装配成最终 prompt。
- `context snapshot` 记录 conversation history、runtime state、memory hints、tool state 等输入源。
- `context trace` 记录最终注入了哪些片段、哪些被裁剪、裁剪原因是什么。

这样做的目的，是把上下文从“业务代码里顺手拼一下”变成一条可回放链路。后面查问题时，不只是看最终 prompt，还能看“为什么这个 prompt 得到的是这些上下文”。

### Context 和 Prompt 的区别

这两个东西的边界要分清：

- `context` 管“这次 turn 里有哪些材料可用”。
- `prompt` 管“把这些材料按什么方式组装成最终发给模型的东西”。

可以直接记成：

- `context` 是原料，决定给模型什么输入。
- `prompt` 是配方，决定输入怎么拼、拼成什么结构、用了哪版规则。

职责上可以这样看：

- `Context management`
  - 关心 conversation history、runtime state、memory、tool result、skill state。
  - 关心裁剪、截断、优先级、可见范围。
  - 输出的是上下文快照或上下文片段集合。
- `Prompt system`
  - 关心 base_system、developer、runtime、tool_instruction、thinking 等 prompt 层。
  - 关心这些层的装配顺序和版本。
  - 输出的是最终 prompt。

一个简单例子：

- `context` 决定：这次只带最近 12 条消息、带上 memory #3、带上当前 tool 结果。
- `prompt` 决定：把这些内容放进 `runtime` / `memory` / `tool_instruction` 哪些层，最后怎么拼给 provider。

### Context 结构

`context` 不是单一文本，它是一组按来源组织的输入片段。它描述的是“这次 turn 能看到什么”。

建议分这些层：

- `conversation_history`：当前会话历史消息。
- `runtime_state`：入口参数、当前任务、运行时状态。
- `memory_state`：从 memory platform 注入的长期记忆或短期记忆。
- `tool_state`：工具结果、工具可用性、工具约束。
- `skill_state`：当前启用的 skill 和 skill 相关上下文。
- `provider_state`：和 provider 适配相关的上下文，例如模型能力、限制和请求格式。
- `policy_state`：裁剪、截断、优先级、敏感信息过滤规则。

这几层的原则是：

- 来源明确。
- 职责单一。
- 最终上下文由 snapshot + policy + 当前 turn 上下文组合出来，而不是在业务路径里四处拼。

### Context 的装配规则

建议固定为以下顺序：

1. conversation history
2. runtime state
3. memory state
4. skill state
5. tool state
6. provider / policy adjustments

原则：

- 越稳定、越基础的上下文越靠前。
- 越依赖当前 turn 的内容越靠后。
- 裁剪规则必须显式记录，不允许“因为代码顺序”导致行为变化。

### 总体链路

```text
User
  -> CLI / Desktop UI
  -> AIAgent
  -> ContextRepo.get_snapshot(conversation_id)
  -> ContextPolicy 计算可见范围 / 裁剪规则
  -> ContextComposer 生成 context bundle
  -> PromptComposer 将 context bundle 叠进 prompt bundle
  -> Provider
  -> AIAgent
  -> ContextRepo.record_trace(...)
```

这条链路说明的是：

- `context` 先决定“有哪些输入可用”。
- `prompt` 再决定“如何把这些输入装配成最终请求”。
- `provider` 只消费最终请求，不关心前面的装配细节。

### 时序说明

#### 运行流

```text
User
  -> CLI / Desktop UI
  -> AIAgent
  -> ContextRepo.get_snapshot(conversation_id)
  -> ContextPolicy 计算可见范围 / 裁剪规则
  -> ContextComposer 生成 context bundle
  -> PromptComposer 将 context bundle 叠进 prompt bundle
  -> Provider
  -> AIAgent
  -> ContextRepo.record_trace(...)
```

要点：

- `ContextRepo` 负责取上下文快照和落 trace。
- `ContextPolicy` 负责裁剪和优先级。
- `ContextComposer` 负责把上下文拼成稳定输入。
- `PromptComposer` 继续负责最终 prompt 装配。

#### 管理流

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> ContextService
  -> ContextRepo
  -> SQLite DB
```

按功能拆开看：

- 查看
  - `list_context_snapshots`
  - `get_context_snapshot`
  - `list_context_traces`
  - `inspect_context`
- 生成
  - `build_context_snapshot(conversation_id, turn_id?)`
- 裁剪
  - `apply_context_policy(snapshot)`
- 记录
  - `record_context_trace(...)`

### 本阶段范围

- conversation history
- runtime context
- memory context
- skill context
- tool context
- context 裁剪与截断
- context trace
- context 与 prompt 的衔接

### 本阶段不做

- `Milestone A3: Memory platform`
- `Milestone A4: Tool management`
- `Milestone A5: Provider management`
- `Milestone A6: Agent and task management`
- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`

### 执行顺序

1. 先定义 `context snapshot` / `context trace` 的 schema 和 repo。
2. 再把 conversation history 装配成稳定的 context bundle。
3. 接入 runtime / memory / skill / tool 的上下文输入。
4. 补 CLI 和 sidecar 的只读查看命令。
5. 补测试和一个可跑通的 smoke demo。
6. 确认 A2 收口后，再进入 `Memory platform`。

### 验收标准

- 一次 turn 结束后，能查询这次 turn 的 context trace。
- trace 里能看到来源片段、裁剪规则、最终上下文大小。
- context 的输入顺序稳定，后续版本可以在 policy 上演进，而不是散落在业务代码里。
- `context` 和 `prompt` 的边界清楚，前者管输入，后者管装配。
- 现有聊天和 prompt trace 不回退。

### 验收方法

先跑自动化测试，再做一次手工检查。

```powershell
uv run pytest tests/agent/test_context_system.py -q
uv run pytest tests/platform/test_context_foundations.py -q
```

手工演示建议：

```powershell
uv run chariot chat --conversation new "请总结我们当前讨论的目标"
uv run chariot context list
uv run chariot context inspect <conversation_id>
uv run chariot context traces
```

手工验收时重点看四件事：

- `context build/inspect/traces` 能否看到上下文快照和裁剪结果。
- `context` 是否能稳定进入 `prompt`，并和 A1 的 `prompt trace` 对齐。
- conversation history、runtime、memory、tool、skill 的优先级是否符合预期。
- 这条 context trace 是否和实际聊天行为对应，而不是一条孤立的记录。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把后续闭环能力塞进来。
- 新计划必须在用户确认后再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。
