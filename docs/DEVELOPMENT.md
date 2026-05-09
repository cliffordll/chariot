# Development

> 本文只讲阶段设计、执行顺序和验收方式。  
> 长期规则见 `RULES.md`。总体架构见 `ARCHITECTURE.md`。

## 1. 执行顺序

建议严格按下面顺序推进：

1. Phase 0: 重命名与术语收口
2. Phase 1: 主循环与 provider
3. Phase 2: runtime 与 sidecar
4. Phase 3: tool execution 与治理骨架
5. Phase 4: data 与平台基础设施
6. Phase 5: frontend 与 compat 收口

原因：

- 命名先清，后面结构重构才不会一边改逻辑一边猜语义
- 主循环/provider 是所有平台能力的基础
- sidecar/runtime 决定配置和运行时是否一致
- tool/governance 是自主进化的执行基础
- data 层决定平台能力如何落地
- frontend/compat 最后收，避免前期界面频繁跟着抖动

## 2. 阶段设计

### 2.1 Phase 0: 重命名与术语收口

阶段目标：

- 统一主术语
- 统一高频入口命名
- 统一更新动词
- 把后续结构重构需要依赖的命名先清干净

阶段原则：

- 只改高收益命名，不做全仓大扫除
- 只改会持续误导阅读和设计的名字
- 改名必须成批落地，避免新旧名字长期并存

阶段范围：

- `provider` / `model` 概念收口
- `convo` / `conversation` 概念收口
- `edit_*` / `update_*` 动词收口
- 顶层 `run` 命名收口

本阶段直接改名：

1. `AIAgent.run` -> `AIAgent.run_chat`
2. `AgentLoop.run` -> `AgentLoop.stream_chat`
3. `_run_stateless` -> `_run_stateless_chat`
4. `_run_stateful` -> `_run_stateful_chat`
5. `_stateful_critical_section` -> `_run_stateful_turn`
6. `_build_next_req` -> `_build_next_request`
7. `_buffer_event` -> `_apply_stream_event`
8. `edit_provider` -> `update_provider`

本阶段不做：

- 低收益命名全仓清理
- 目录级重构
- 平台子系统新增

交付结果：

- 主循环相关命名清楚
- provider / convo 主术语稳定
- 后续阶段不再被历史命名拖住

手动验收 demo：

1. 查看 CLI 帮助，确认主入口和子命令还正常：

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

验收要求：

- 重命名涉及模块的单测全过
- grep 检查旧主名没有继续在主路径扩散
- 手动验收 demo 跑通

### 2.2 Phase 1: 主循环与 Provider 收口

阶段目标：

- 稳定主循环和 provider contract
- 为后续 `memory / skills / eval / delegation` 打基础

阶段原则：

- 先收 provider contract，再扩功能
- 先收边界，再做 memory / skills / eval
- 结构收口优先于功能扩张

结构边界：

- `AIAgent`
  - 负责外层 orchestration
  - 负责 provider routing
  - 负责 stateful / stateless 分流
  - 负责 history 装配
  - 负责未来的 memory / skill / guardrail 注入点
- `AgentLoop`
  - 负责标准化后的 chat/tool loop 执行
  - 消费 provider stream
  - 执行 tool calls
  - 生成下一轮 request

本阶段要补的能力：

- request normalization
- provider capabilities
- provider event normalizer / validator

本阶段不做：

- memory 落库
- skills
- eval
- delegation
- approval / checkpoint

交付结果：

- provider 与 loop 的契约清楚
- 主循环不再直接暴露 provider 差异
- `AIAgent` 与 `AgentLoop` 职责边界更清楚

手动验收 demo：

1. 跑一次 stateless chat：

```powershell
uv run chariot chat "hello"
```

2. 新建并继续一轮 stateful chat：

```powershell
uv run chariot chat --convo new "first turn"
uv run chariot convo list
uv run chariot chat --convo <ULID> "second turn"
uv run chariot convo show <ULID>
```

3. 启用一个工具并验证 tool loop 基本路径：

```powershell
uv run chariot tool enable list_dir
uv run chariot chat --convo new "请列出当前目录"
uv run chariot tool disable list_dir
```

4. 运行核心测试：

```powershell
uv run pytest tests/agent tests/providers tests/sidecar/test_chat_method.py
```

5. 跑一轮主循环相关 smoke：

```powershell
uv run pytest tests/agent/test_loop.py -q
```

验收要求：

- `tests/agent/*` 通过
- `tests/providers/*` 通过
- `tests/sidecar/test_chat_method.py` 通过
- 手动验收 demo 跑通

### 2.3 Phase 2: Runtime 与 Sidecar 收口

阶段目标：

- 让配置变更和 runtime 状态更一致
- 把 sidecar 从“method 堆业务”收成分层结构

阶段原则：

- sidecar 先分层，再继续加 method
- 配置变更必须逐步走向 runtime 可热生效
- service 边界先于功能扩张稳定下来

结构边界：

- sidecar 形成 `decoder -> service -> rpc adapter`
- provider runtime 支持 `reload / invalidate`
- 配置对象和运行时对象边界清楚

本阶段命名要求：

- 新方法统一用 `update_*`
- 新编排层统一用 `*Service`
- 运行时管理统一用 `*Runtime` / `*Registry`

如果对应文件正在重构，可顺手改：

- `_resolve_agent`
- `_session`

交付结果：

- sidecar 业务逻辑不再继续膨胀
- provider 配置修改后可热生效

手动验收 demo：

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

4. 运行 sidecar 测试：

```powershell
uv run pytest tests/sidecar -q
```

5. 运行 provider 相关测试，确认 runtime reload 相关逻辑没有打坏已有路径：

```powershell
uv run pytest tests/providers -q
```

验收要求：

- `tests/sidecar/*` 通过
- runtime reload 相关新增测试通过
- 手动验收 demo 跑通

### 2.4 Phase 3: Tool Execution 与治理骨架

阶段目标：

- 给 tool execution 补治理接口
- 为自主进化能力建立安全执行外壳

阶段原则：

- 先给 tool execution 挂治理接口，再做更强自治能力
- policy / audit / checkpoint 先留接口，再逐步强化实现
- 避免把治理逻辑散落到每个 tool

结构边界：

- tool execution 增加 policy hook
- 增加 audit hook
- 预留 approval hook
- 预留 checkpoint hook

本阶段新命名直接规范：

- `ToolPolicy`
- `ToolExecutionService`
- `ToolAuditService`
- `execute_tool_call`
- `record_tool_event`

本阶段顺手改名：

- `_execute_tools` -> `_execute_tool_calls`
- `_execute_one_tool` -> `_execute_tool_call`

交付结果：

- 工具执行链条可审计、可插 policy
- 后续 guardrail / approval 能自然接入

手动验收 demo：

1. 启用一个真实工具并直接使用：

```powershell
uv run chariot tool enable read_file
uv run chariot chat --convo new "请读取 README.md"
uv run chariot tool disable read_file
```

2. 配置一个带约束的工具并使用：

```powershell
uv run chariot tool enable http_get
uv run chariot tool config http_get -o "allowed_domains=[\"example.com\"]"
uv run chariot chat --convo new "请请求 https://example.com"
uv run chariot tool disable http_get
```

3. 运行 loop 测试：

```powershell
uv run pytest tests/agent/test_loop.py -q
```

4. 运行 CLI 和 tool 相关测试：

```powershell
uv run pytest tests/cli tests/agent/test_loop.py -q
```

验收要求：

- `tests/agent/test_loop.py` 通过
- tools 相关新增测试通过
- policy / audit hook tests 通过
- 手动验收 demo 跑通

### 2.5 Phase 4: 平台基础设施子系统

阶段目标：

- 建立 `memory / eval / audit / skills / checkpoints` 的正式基础设施

阶段原则：

- 先把平台数据结构落稳，再扩展上层能力
- 新子系统从一开始就按 `Row / Repo / Service / Registry` 分层
- 避免把 memory / eval / audit 继续塞回现有 runtime 目录

结构边界：

- 新增对应子系统目录
- 扩展数据库 schema
- 建立 repo / service / registry 的层次

建议直接使用：

- `MemoryRow`
- `MemoryRepo`
- `EvalRunRow`
- `EvalCaseRow`
- `AuditEventRow`
- `CheckpointRow`
- `SkillRow`

交付结果：

- 平台关键能力有正式落点
- 后续自主进化不再靠零散补丁推进

手动验收 demo：

1. 跑 migration 和全量核心测试：

```powershell
uv run pytest tests -q
```

2. 本阶段新增子系统后，应提供最小 CLI 或 sidecar 验收命令。建议至少补齐下面这些命令再进入验收：

```powershell
uv run chariot memory list
uv run chariot eval run <suite-or-case>
uv run chariot skill list
uv run chariot checkpoint list
```

3. 如果新增了独立子系统测试目录，再单独跑一遍：

```powershell
uv run pytest tests/memory tests/eval tests/sidecar -q
```

验收要求：

- migrations tests 通过
- repo tests 通过
- 新子系统测试通过
- 手动验收 demo 跑通

### 2.6 Phase 5: Frontend 与兼容层收口

阶段目标：

- 收口旧兼容接口
- 前端页面按平台能力继续扩展

阶段原则：

- 先收 API 命名，再扩页面
- compat alias 只做过渡，不再扩散
- 前端拆分围绕 state 和 boundary，不围绕视觉块硬拆

结构边界：

- 拆 `Chat.tsx`
- API 层拆成 `rpc client + domain api`
- compat alias 逐步退场
- 新页面按 `provider / convo` 新主名构建

本阶段改名：

- 停用 `listModels / createModel / updateModel / deleteModel`
- 停用 `Conversation` 作为主名

统一收口到：

- `listProviders`
- `addProvider`
- `updateProvider`
- `deleteProvider`
- `Convo`

交付结果：

- 前端不再同时维护两套概念命名
- 后面可以继续加 `memory / skills / eval / audit` 页面

手动验收 demo：

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

验收要求：

- frontend build 通过
- chat 页面 smoke 测试通过
- compat regression checks 通过
- 手动验收 demo 跑通

## 3. 立即开始的任务

现在先进入 Phase 0，顺序如下：

1. 落地第一批高收益重命名
2. 清理 `provider / model`、`convo / conversation` 的主术语混用
3. 清理 `edit_* / update_*` 混用
4. 确认主循环和 sidecar 入口的新命名全部打通

Phase 0 完成后，再进入 Phase 1。
