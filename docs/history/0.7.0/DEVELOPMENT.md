# Development

> 本文只讲阶段设计、执行顺序和验收方式。
> 长期规则见 `RULES.md`。总体架构见 `ARCHITECTURE.md`。
> 完成 `Phase 0 ~ Phase 5` 后，后续平台能力建设见 `EVOLUTION_PLAN.md`。

## 0. Phase 状态

- `Phase 0`: 待开始
- `Phase 1`: 待开始
- `Phase 2`: 待开始
- `Phase 3`: 待开始
- `Phase 4`: 待开始
- `Phase 5`: 待开始

状态只在本节更新；各 Phase 正文保留完整描述，不删历史。

## 1. 执行顺序

建议严格按下面顺序推进：

1. `Phase 0`: 重命名与术语收口
2. `Phase 1`: 主循环与 provider
3. `Phase 2`: runtime 与 sidecar
4. `Phase 3`: tool execution 重构
5. `Phase 4`: 平台基础设施最小落点
6. `Phase 5`: frontend 与 compat 收口

原因：

- 命名先清，后面结构重构才不会一边改逻辑一边猜语义。
- 主循环与 provider contract 是所有平台能力的基础。
- sidecar/runtime 决定配置和运行时是否一致。
- tool execution 先收敛，后续治理子系统才有稳定接入点。
- 平台子系统先落 `schema + repo`，避免闭环能力和底座混做。
- frontend/compat 最后收，避免前期界面频繁跟着抖动。

## 2. 通用执行规则

所有 Phase 都遵循下面规则：

1. 一个 Phase 完成后，再进入下一个 Phase。
2. 每个 Phase 的验收，除了跑本 Phase demo，还必须确认前序 Phase 的 smoke demo 仍然跑通。
3. 每个 Phase 都要先写清楚“当前现状”，基于实际 `grep` 和代码阅读，不凭印象。
4. 不把“未来会用到的完整闭环”提前塞进当前 Phase；只做当前 Phase 真正需要的最小边界。

## 3. 阶段设计

### 3.1 Phase 0: 重命名与术语收口

阶段目标：

- 统一主术语。
- 统一高频入口命名。
- 统一更新动词。
- 把后续结构重构依赖的高收益命名先收干净。

#### 3.1.1 当前现状

基于当前代码可确认：

- `AIAgent.run`、`AgentLoop.run` 仍在使用。
- `_run_stateless`、`_run_stateful`、`_stateful_critical_section` 仍在使用。
- `edit_provider` 仍在 sidecar 路径中使用。
- 前端与 CLI 仍残留 `conversation`、`listModels` 等旧主名。

#### 3.1.2 阶段原则

- 只改高收益命名，不做全仓大扫除。
- 只改跨文件接口名和高频入口名。
- 局部 helper 名不单独起一个 Phase，只有在对应文件本来就要改时顺手改。
- 改名必须成批落地，避免新旧名字长期并存。

#### 3.1.3 阶段范围

- `provider` / `model` 概念收口。
- `conversation` / `conversation` 概念收口。
- `edit_*` / `update_*` 动词收口。
- 顶层 `run` 命名收口。

本阶段直接改名：

1. `AIAgent.run` -> `AIAgent.run_chat`
2. `AgentLoop.run` -> `AgentLoop.stream_chat`
3. `_run_stateless` -> `_run_stateless_chat`
4. `_run_stateful` -> `_run_stateful_chat`
5. `_stateful_critical_section` -> `_run_stateful_turn`
6. `edit_provider` -> `update_provider`

#### 3.1.4 本阶段不做

- `_build_next_req`、`_buffer_event` 这类局部 helper 单独改名。
- 低收益命名全仓清理。
- 目录级重构。
- 新平台子系统。

#### 3.1.5 交付结果

- 主循环相关高频入口命名清楚。
- `provider` / `conversation` 主术语稳定。
- 后续 Phase 不再被历史主名拖住。

#### 3.1.6 手动验收 demo

1. 查看 CLI 帮助，确认主入口和子命令仍正常：

```powershell
uv run chariot --help
uv run chariot chat --help
uv run chariot provider --help
```

2. 运行测试：

```powershell
uv run pytest tests/agent tests/providers tests/sidecar/test_chat_method.py
```

3. 搜索旧主名残留：

```powershell
rg "edit_provider|conversation|class AIAgent.*run\(|class AgentLoop.*run\(" chariot packages tests
```

#### 3.1.7 验收要求

- 重命名涉及模块的单测全过。
- `grep` 检查旧主名没有继续在主路径扩散。
- 本阶段 demo 跑通。

### 3.2 Phase 1: 主循环与 provider 收口

阶段目标：

- 稳定主循环和 provider contract。
- 为后续 `memory / skills / eval / delegation` 打基础。

#### 3.2.1 当前现状

当前代码已经有明确主链路：

- `AIAgent` 负责外层 orchestration。
- `AgentLoop` 负责 provider stream 和 tool loop。
- 但主循环对 provider 原始事件顺序仍较敏感。
- request normalization / capability / event normalization 仍未显式成型。

#### 3.2.2 阶段原则

- 先收 provider contract，再扩功能。
- 先收边界，再做 memory / skills / eval。
- 结构收口优先于功能扩张。
- Phase 0 没收完的局部 helper 名，可在本阶段顺手改。

#### 3.2.3 结构边界

- `AIAgent`
  - 负责外层 orchestration
  - 负责 provider routing
  - 负责 stateful / stateless 分流
  - 负责 history 装配
  - 负责未来的 memory / skill / guardrail 注入点
- `AgentLoop`
  - 负责标准化后的 chat / tool loop 执行
  - 消费 provider stream
  - 执行 tool calls
  - 生成下一轮 request

#### 3.2.4 本阶段要补的能力

- request normalization
- provider capabilities
- provider event normalizer / validator

#### 3.2.5 本阶段不做

- memory 落库
- skills
- eval
- delegation
- approval / checkpoint

#### 3.2.6 交付结果

- provider 与 loop 的契约清楚。
- 主循环不再直接暴露 provider 差异。
- `AIAgent` 与 `AgentLoop` 职责边界更清楚。

#### 3.2.7 手动验收 demo

1. 跑一次 stateless chat：

```powershell
uv run chariot chat "hello"
```

2. 新建并继续一轮 stateful chat：

```powershell
uv run chariot chat --conversation new "first turn"
uv run chariot conversation list
uv run chariot chat --conversation <ULID> "second turn"
uv run chariot conversation show <ULID>
```

3. 启用一个工具并验证 tool loop 基本路径：

```powershell
uv run chariot tool enable list_dir
uv run chariot chat --conversation new "请列出当前目录"
uv run chariot tool disable list_dir
```

4. 运行核心测试：

```powershell
uv run pytest tests/agent tests/providers tests/sidecar/test_chat_method.py
uv run pytest tests/agent/test_loop.py -q
```

#### 3.2.8 验收要求

- `tests/agent/*` 通过。
- `tests/providers/*` 通过。
- `tests/sidecar/test_chat_method.py` 通过。
- 本阶段 demo 跑通。
- `Phase 0` 的 CLI / grep / 核心测试 smoke 仍能跑通。

### 3.3 Phase 2: Runtime 与 sidecar 收口

阶段目标：

- 让配置变更和 runtime 状态更一致。
- 把 sidecar 从“methods 堆业务”收成分层结构。

#### 3.3.1 当前现状

当前已部分具备分层雏形：

- `sidecar/methods/__init__.py` 已有集中注册和共享基类思路。
- `sidecar/methods/chat.py` 已有 `_RequestDecoder`。
- 也就是说 `decoder` 这层不是从零开始。
- 真正未完成的是 `service` 层收拢和 runtime reload / invalidate 的一致边界。

#### 3.3.2 阶段原则

- sidecar 先分层，再继续加 method。
- 配置变更必须逐步走向 runtime 可热生效。
- service 边界先于功能扩张稳定下来。

#### 3.3.3 结构边界

- sidecar 形成 `decoder -> service -> rpc adapter`
- provider runtime 支持 `reload / invalidate`
- 配置对象和运行时对象边界清楚

#### 3.3.4 命名要求

- 新方法统一用 `update_*`
- 新编排层统一用 `*Service`
- 运行时管理统一用 `*Runtime` / `*Registry`

如果对应文件正在重构，可顺手改：

- `_resolve_agent`
- `_session`

#### 3.3.5 交付结果

- sidecar 业务逻辑不再继续膨胀。
- provider 配置修改后可热生效。

#### 3.3.6 手动验收 demo

1. 查看当前 provider 和默认 provider：

```powershell
uv run chariot provider list
uv run chariot provider show
```

2. 修改一个 provider 后重新验证：

```powershell
uv run chariot provider probe mock
uv run chariot provider edit mock -p temperature=0.2
uv run chariot provider show mock
uv run chariot provider probe mock
```

3. 如果本阶段加入了热生效路径，再验证“不重启 sidecar 配置已生效”：

```powershell
bun run --filter=@chariot/desktop tauri dev
```

4. 运行 sidecar 和 provider 相关测试：

```powershell
uv run pytest tests/sidecar -q
uv run pytest tests/providers -q
```

#### 3.3.7 验收要求

- `tests/sidecar/*` 通过。
- runtime reload 相关新增测试通过。
- 本阶段 demo 跑通。
- `Phase 0` 与 `Phase 1` 的 smoke 仍能跑通。

### 3.4 Phase 3: Tool execution 重构

阶段目标：

- 把 tool execution 从散落逻辑收成稳定 call-site。
- 给后续治理子系统留出自然接入点，但不提前发明整套闭环协议。

#### 3.4.1 当前现状

当前工具路径已经能跑通，但问题主要在：

- tool execution 仍偏向“能执行”，而不是“可治理”。
- `_execute_tools` / `_execute_one_tool` 之类逻辑仍在主循环内聚集。
- audit / approval / checkpoint 的真实落点还不存在。

#### 3.4.2 阶段原则

- 先做执行链路收敛，再接治理子系统。
- 不预设过厚的 policy / audit / checkpoint 协议。
- 避免把治理逻辑散落到每一个 tool。

#### 3.4.3 结构边界

- 统一收敛到稳定执行入口，例如 `ToolExecutionService.execute_tool_call(...)`
- 主循环只负责调度，不负责承载越来越多工具治理细节
- 允许保留薄的扩展点，但不要求本阶段把 hook 体系做完整

#### 3.4.4 本阶段建议命名

- `ToolExecutionService`
- `execute_tool_call`
- `_execute_tools` -> `_execute_tool_calls`
- `_execute_one_tool` -> `_execute_tool_call`

#### 3.4.5 本阶段不做

- 完整 `ToolPolicy` 子系统
- 完整 `ToolAuditService`
- approval workflow
- checkpoint records

这些在 Phase 4 落完最小基础设施后，再进入 `EVOLUTION_PLAN.md` 对应闭环能力。

#### 3.4.6 交付结果

- 工具执行链路有稳定入口。
- 后续 audit / approval / checkpoint 能自然接入。
- 主循环继续保持可读，不再无限膨胀。

#### 3.4.7 手动验收 demo

1. 启用一个真实工具并直接使用：

```powershell
uv run chariot tool enable read_file
uv run chariot chat --conversation new "请读一下 README.md"
uv run chariot tool disable read_file
```

2. 配置一个带约束的工具并使用：

```powershell
uv run chariot tool enable http_get
uv run chariot tool config http_get -o 'allowed_domains=["example.com"]'
uv run chariot tool config http_get -o "allowed_domains=[example.com]"
uv run chariot tool config http_get -o 'allowed_domains=[example.com]'
uv run chariot tool config http_get -o "allowed_domains=[\"example.com\"]" 这种不行
uv run chariot chat --conversation new "请请求 https://example.com"
uv run chariot tool disable http_get
```

3. 运行 loop / CLI 相关测试：

```powershell
uv run pytest tests/agent/test_loop.py -q
uv run pytest tests/cli tests/agent/test_loop.py -q
```

#### 3.4.8 验收要求

- `tests/agent/test_loop.py` 通过。
- tools 相关新增测试通过。
- 本阶段 demo 跑通。
- `Phase 0 ~ Phase 2` 的 smoke 仍能跑通。

### 3.5 Phase 4: 平台基础设施最小落点

阶段目标：

- 为 `memory / eval / audit / skills / checkpoints` 建立最小物理基础。
- 只落 `schema + repo`，不在本阶段追求闭环能力成型。

#### 3.5.1 当前现状

当前仓库还没有把这些平台子系统显式长成独立模块：

- memory 还不是正式长期记忆子系统
- audit / trace 还没有统一数据落点
- eval / skills / checkpoints 也没有稳定基础表和 repo 边界

#### 3.5.2 阶段原则

- 先把平台数据结构落稳，再扩展上层能力。
- 每个子系统本阶段只要求 `Row + Repo + migration`。
- 不把 `Service / Registry / 闭环逻辑` 一口气塞进同一个 Phase。
- 本阶段是 `EVOLUTION_PLAN.md` 的物理基础，不是闭环实现阶段。

#### 3.5.3 结构边界

- 新增对应子系统目录
- 扩展数据库 schema
- 建立最小 repo 层

建议直接落：

- `MemoryRow`
- `MemoryRepo`
- `EvalRunRow`
- `EvalCaseRow`
- `EvalRepo`
- `AuditEventRow`
- `AuditRepo`
- `CheckpointRow`
- `CheckpointRepo`
- `SkillRow`
- `SkillRepo`

#### 3.5.4 本阶段不做

- memory retrieval / injection / consolidation
- trace query platform
- skill proposal / update loop
- eval suite orchestration
- approval / rollback workflow

这些统一交给 `EVOLUTION_PLAN.md` 的后续 milestone。

#### 3.5.5 交付结果

- 平台关键对象有正式落点。
- 后续自主进化不再依赖零散补丁推进。
- 上层闭环能力有明确可接的底座。

#### 3.5.6 手动验收 demo

1. 跑 migration 和全量核心测试：

```powershell
uv run pytest tests -q
```

2. 本阶段新增子系统后，至少提供最小 CLI 或 sidecar 验收命令：

```powershell
uv run chariot memory list
uv run chariot eval list
uv run chariot skill list
uv run chariot checkpoint list
```

3. 如果新增了独立子系统测试目录，再单独跑一遍：

```powershell
uv run pytest tests/memory tests/eval tests/sidecar -q
```

#### 3.5.7 验收要求

- migration tests 通过。
- repo tests 通过。
- 新子系统测试通过。
- 本阶段 demo 跑通。
- `Phase 0 ~ Phase 3` 的 smoke 仍能跑通。

### 3.6 Phase 5: Frontend 与兼容层收口

阶段目标：

- 收口旧兼容接口。
- 前端页面按平台能力继续扩展。

#### 3.6.1 当前现状

当前前端与 API 层仍有明显旧主名残留：

- `listModels / createModel / updateModel / deleteModel`
- `Conversation` 作为主名
- 多处 `conversation` 系列调用仍在 UI 路径中使用

#### 3.6.2 阶段原则

- 先收 API 命名，再扩页面。
- compat alias 只做过渡，不再继续扩散。
- 前端拆分围绕 state 和 boundary，不围绕视觉块硬拆。

#### 3.6.3 结构边界

- 拆 `Chat.tsx`
- API 层拆成 `rpc client + domain api`
- compat alias 逐步退场
- 新页面按 `provider / conversation` 新主名构建

#### 3.6.4 本阶段改名

- 停用 `listModels / createModel / updateModel / deleteModel`
- 停用 `Conversation` 作为主名

统一收口到：

- `listProviders`
- `addProvider`
- `updateProvider`
- `deleteProvider`
- `Conversation`

#### 3.6.5 交付结果

- 前端不再同时维护两套概念命名。
- 后续可以继续加 `memory / skills / eval / audit` 页面。

#### 3.6.6 手动验收 demo

1. 构建前端：

```powershell
bun run --filter=@chariot/app build
```

2. 启动桌面端联调：

```powershell
bun run --filter=@chariot/desktop tauri dev
```

3. 打开应用后，实际走一遍：

- Chat 页面新建会话并发送一条消息
- 切到已有会话，确认历史正常显示
- 在 Providers 页面修改一个 provider，再回到 Chat 页面确认仍能使用
- 检查页面和调用路径里不再依赖旧主名 `listModels`、`Conversation`

#### 3.6.7 验收要求

- frontend build 通过。
- chat 页面 smoke 测试通过。
- compat regression checks 通过。
- 本阶段 demo 跑通。
- `Phase 0 ~ Phase 4` 的 smoke 仍能跑通。

## 4. 立即开始的任务

现在先进入 `Phase 0`，顺序如下：

1. 落地第一批高收益重命名。
2. 清理 `provider / model`、`conversation / conversation` 的主术语混用。
3. 清理 `edit_* / update_*` 混用。
4. 确认主循环和 sidecar 入口的新命名全部打通。

`Phase 0` 完成后，再进入 `Phase 1`。
