# Development Review

> 本文是对 `DEVELOPMENT.md` 的评审意见。
>
> 评审依据:实际 grep 当前代码,对照 `DEVELOPMENT.md` 的诊断点和阶段划分,
> 检查"按这个文档跑能不能不回头"。
>
> 本文不重写 `DEVELOPMENT.md`,只列出建议改动。是否落地由作者决定。

## 提纲

如果只想先抓重点,先看这一节。

### 这份评审的核心结论

`DEVELOPMENT.md` 诊断准、顺序对、边界清,但有四块**结构性问题**会让执行
过程中两次回头改同一批文件,且 `Phase 4` 一开工就会变成无尽 Phase。

总评:**按现状直接执行能跑,中途会重复返工;调整后可避免。**

### 这份评审怎么读

- 想先看实证:看 `1. 当前代码状态对照`
- 想先看做得好的部分:看 `2. 优点`
- 想先看必须调整的部分:看 `3. 结构性问题`
- 想先看具体怎么改:看 `4. 综合建议`
- 想先看最终结论:看 `5. 当前结论`

## 1. 当前代码状态对照

`DEVELOPMENT.md` 列的诊断点,本次评审用 `grep` 在仓库里逐条核对,
**全部命中**。代码与文档诊断一致,文档不是凭印象写的。

### 1.1 Phase 0 八条重命名 — 全部存在

- `AIAgent.run` — `chariot/agent/run.py:167`
- `AgentLoop.run` — `chariot/agent/loop.py:75`
- `_run_stateless` — `chariot/agent/run.py:196`
- `_run_stateful` — `chariot/agent/run.py:209`
- `_stateful_critical_section` — `chariot/agent/run.py:240`
- `_buffer_event` — `chariot/agent/loop.py:127`
- `_build_next_req` — `chariot/agent/loop.py:295`
- `edit_provider` — `chariot/sidecar/methods/__init__.py:206`、`provider.py:56`

### 1.2 旧主名残留 — 前端大面积使用

`packages/app/src/` 仍在用:

- `api.listModels()` — `Chat.tsx:164`、`Providers.tsx:153 / 633 / 809 / 811 / 984`
- `api.listConversations()` / `getConversation()` / `createConversation()` /
  `deleteConversation()` / `updateConversationTitle()` — `Chat.tsx:176 / 189 / 260 / 285 / 317`
- `Conversation` 类型 alias — `lib/api.ts:277 / 279 / 285 / 331 ~ 335`

CLI 端 `cli/__main__.py` / `cli/repl.py` / `cli/render.py` 仍有 `conversation`
散文残留(`commands/convo.py:32` 注释里还在用)。

### 1.3 Phase 2 已部分超前

文档说 Phase 2 要做"sidecar `decoder → service → rpc adapter` 三层",
但实际看代码:

- `chariot/sidecar/methods/__init__.py` 已有 `SidecarAgent Protocol` +
  `MethodBase` 共享基 + `register_methods` 集中注册
- `chariot/sidecar/methods/chat.py` 已有 `_RequestDecoder.parse(params)`,
  decoder 层已拆出去

**Phase 2 三层里 decoder 已基本完成**。文档里的"待做清单"会让读者重复
评估已完成的工作。

## 2. 优点

### 2.1 诊断准

8 条命名 + sidecar 分层 + tool 治理空缺 + 前端旧名,**实测全部存在**。
不是 "听起来对" 而是 "查得到对"。

### 2.2 阶段顺序合理

`命名 → 主循环 → runtime → tool 治理 → 平台基础 → 前端` 这个顺序符合
"底层先稳后前端跟" 的常识,也跟 `ARCHITECTURE.md` 的 "保住主链路 + 收紧
边界 + 长出平台子系统" 节奏一致。

### 2.3 文档职责正交

四份文档分工清楚:

- `DEVELOPMENT.md` — 当前阶段做什么
- `ARCHITECTURE.md` — 系统该长什么样
- `RULES.md` — 长期命名 / 动词 / 缩写规则
- `EVOLUTION_PLAN.md` — 重构完成后做什么

不互相挤占,这种结构在多人协作场景下抗腐烂。

### 2.4 每 Phase 有 "不做" 段 + 手动验收 demo

边界明确,验收点具体到 `uv run chariot ...` 命令。不会出现 "Phase 完成了
但说不清做了什么" 的情况。

## 3. 结构性问题

按重要性排序。每条按 `现象 / 影响 / 建议` 三段式。

### 3.1 Phase 0 / Phase 1 切分点不舒服

**现象**

Phase 0 改的文件:`agent/run.py`(`AIAgent.run` / `_run_stateless` /
`_run_stateful` / `_stateful_critical_section`)、`agent/loop.py`
(`AgentLoop.run` / `_buffer_event` / `_build_next_req`)。

Phase 1 改的文件:还是 `agent/run.py` + `agent/loop.py`(主循环 + provider
contract 收口)。

**影响**

同一批文件先 Phase 0 改名 commit,再 Phase 1 改 contract commit。
git log 上紧挨着两个 commit 都动同一批行 — 双倍 review 成本。

而且 `RULES.md §6` 写 "低收益重命名不单独发起,只在文件正在改时顺手改"。
Phase 0 把 `_buffer_event / _build_next_req` 这种**局部 helper 名**单独
成 Phase,跟自己定的规则有点拧。

**建议**

Phase 0 收窄到**跨文件接口名**:

- `AIAgent.run → run_chat`
- `AgentLoop.run → stream_chat`
- `edit_provider → update_provider`
- `_run_stateless → _run_stateless_chat`
- `_run_stateful → _run_stateful_chat`
- `_stateful_critical_section → _run_stateful_turn`

这些影响调用方,值得单独成批。

局部 `_xxx` helper 名(`_buffer_event` / `_build_next_req`)留给
Phase 1 在改 contract 时**顺手改**。这样:

- Phase 0 = 一个跨仓 grep + 替换 commit,验证零功能改动
- Phase 1 = 一个主循环改造 commit,局部命名跟随

### 3.2 Phase 3 hook 跟 Phase 4 子系统顺序错位

**现象**

- Phase 3 要"在 tool execution 预留 policy / audit / approval / checkpoint hook"
- Phase 4 才建 `MemoryRow` / `EvalRow` / `AuditEventRow` /
  `CheckpointRow` 子系统

**影响**

Phase 3 预留的 hook 接口在没有真实落点的情况下**凭空设计**,
Phase 4 建子系统时**几乎一定会改 Phase 3 的 hook 协议** — 二次对齐成本。

**建议**

二选一:

- 方案 A:把 Phase 4 的 audit + checkpoint 提前到 Phase 3,合并成
  "tool 执行 + 治理子系统" 一个大 Phase,hook 和落点一起出
- 方案 B(推荐):Phase 3 **不预留 hook**,只做 call-site 改造
  (把工具执行收进 `ToolExecutionService.execute_tool_call`,但执行链里
  不留 hook 槽),hook 等 Phase 4 子系统建完再回来串

推荐方案 B 的原因:

- Phase 3 主战场是 `chariot/agent/loop.py` 的 `_execute_tools`
- Phase 4 主战场是新子系统目录
- 两边并行做、最后串起来,对单点改动最友好

### 3.3 Phase 4 颗粒太粗

**现象**

Phase 4 一个 Phase 要建 memory + eval + audit + skills + checkpoints
**五个子系统**。对照 `EVOLUTION_PLAN.md`:

- memory → A3(独立 milestone)
- audit → B1 trace platform 的一部分
- eval → B4(独立 milestone)
- skills → B3(独立 milestone)
- checkpoints → B5(独立 milestone 的一部分)

**也就是 DEVELOPMENT 的 Phase 4 = EVOLUTION_PLAN 的 A3 + B1 + B3 + B4 + B5
的简化版**。

**影响**

Phase 4 一旦开工就是一个季度起的工作量,跟 Phase 0 ~ 3 的 "几天到一周"
颗粒度严重不匹配,会出现 "Phase 4 永远完不成" 的问题。

**建议**

Phase 4 缩小到**只做最小骨架**:

- 每个子系统只落 `Row` + `Repo` + migration
- 不做 Service / Registry / 上层逻辑
- 验收标准 = 表能建、能写、能查,**不要求功能闭环**
- 上层逻辑明确推给 `EVOLUTION_PLAN.md` 的 A3 / B1 / B3 / B4 / B5

文档顶部加一句:

> Phase 4 只是 EVOLUTION_PLAN 的物理基础,只落 schema 和 repo,不做闭环。

### 3.4 缺 Phase 之间的衔接机制

三个具体缺失:

#### 3.4.1 没说前序 Phase demo 是否需要在新 Phase 验收时重跑

**现象**

每个 Phase 验收只跑自己的 demo。

**影响**

Phase 1 改了 provider contract 后,Phase 0 的 chat smoke 是不是还过?
Phase 2 改了 sidecar 后,Phase 1 的工具调用 demo 是不是还跑得起来?
没说。Phase 之间走着走着可能就**回归性损坏**。

**建议**

每个 Phase 的"验收要求"加一行:

> 前序 Phase 的 chat / convo / tool smoke demo 必须仍能跑通。

#### 3.4.2 没有 Phase 完成状态标记

**现象**

`CLAUDE.md` 说 "FEATURE.md heading emoji 标进度",但 `DEVELOPMENT.md`
不是 `FEATURE.md`,自己没说怎么标进度。

完成的 Phase 是删除?打勾?另存?**不知道**。

**建议**

加一节 `## 0. Phase 状态`,放在 "1. 执行顺序" 之前:

```
## 0. Phase 状态

- Phase 0:待开始 / 进行中 / 已完成
- Phase 1:待开始
- Phase 2:待开始
- Phase 3:待开始
- Phase 4:待开始
- Phase 5:待开始
```

Phase 完成时只更新这一节,正文 Phase 描述不删 — 保留历史可读性。

#### 3.4.3 跟 EVOLUTION_PLAN 的衔接没在 DEVELOPMENT 里点明

**现象**

`DEVELOPMENT.md` 顶部只引了 `RULES.md` / `ARCHITECTURE.md`,
没引 `EVOLUTION_PLAN.md`。

但 `EVOLUTION_PLAN.md` 自己第 1 节明确写 "前提是 DEVELOPMENT Phase 0 ~ 5
已完成"。`EVOLUTION_PLAN` 知道有 `DEVELOPMENT`,但 `DEVELOPMENT` 不知道有
下家。

**建议**

`DEVELOPMENT.md` 顶部第二行(在 `RULES.md` / `ARCHITECTURE.md` 引用旁)
补一行:

> 完成 Phase 0 ~ Phase 5 后,后续平台能力建设见 `EVOLUTION_PLAN.md`。

### 3.5 Phase 当前进度没体现

**现象**

实际看代码:

- `chariot/sidecar/methods/__init__.py` 已有 `SidecarAgent Protocol` +
  `MethodBase` + `register_methods`
- `chariot/sidecar/methods/chat.py` 已有 `_RequestDecoder`

也就是 Phase 2 三层里 decoder 部分基本做完了。文档没体现 "已部分完成",
读者会重复评估。

**影响**

每次新 Claude 会话进来按文档干活,会从零开始重新设计 decoder,产生
"重新发明轮子"的浪费。

**建议**

每个 Phase 加一段 `### x.y.z 现状(基于实测 grep)`,简短说明已完成 /
未完成 / 已部分超前的部分。比如 Phase 2 应该写:

> 现状:`SidecarAgent Protocol` + `MethodBase` + `_RequestDecoder` 已落
> (`sidecar/methods/__init__.py`、`sidecar/methods/chat.py`);
> service / rpc adapter 层未拆;runtime reload 未做。

注意:**这段必须基于 grep 校准,不能凭印象写**,否则会比没写更糟。

## 4. 综合建议

按改动大小排序,从小到大:

### 4.1 小改动(顶部两行)

- 加 "完成 Phase 0 ~ 5 后转 `EVOLUTION_PLAN.md`" 一行
- 每个 Phase 验收要求加 "前序 Phase smoke demo 必须仍能跑" 一行

### 4.2 小改动(加节)

- 加 `## 0. Phase 状态` 节,显式标进度
- 每个 Phase 加 `### 现状` 段(grep 实测)

### 4.3 中改动(Phase 0 收窄)

- Phase 0 范围限定到**跨文件接口名**
- 局部 helper 名(`_buffer_event` / `_build_next_req`)挪到 Phase 1 顺手改
- Phase 0 验收 = 一次跨仓 grep + 替换,功能零变动

### 4.4 中改动(Phase 3 hook 暂缓)

- Phase 3 不预留 hook,只做 call-site 改造
- hook 等 Phase 4 子系统建完后再串
- 文档明确 "Phase 3 = 重构,Phase 4 = 子系统,串接在 Phase 4 后期"

### 4.5 中改动(Phase 4 颗粒缩小)

- Phase 4 缩成 "只落 `Row` + `Repo` + migration"
- 上层 Service / Registry / 闭环全推 `EVOLUTION_PLAN.md`
- 文档顶部声明 "Phase 4 只是物理基础,不做闭环"

### 4.6 改动优先级建议

如果时间有限,按这个顺序改:

1. `4.1` + `4.2`(顶部两行 + 加节) — 30 分钟
2. `4.5`(Phase 4 缩小) — 1 小时
3. `4.3`(Phase 0 收窄) — 1 小时
4. `4.4`(Phase 3 hook 暂缓) — 1 小时

`4.1 / 4.2 / 4.5` 是最值得先做的 — 改完后整个文档的"按这个跑能不能不
回头"会从 60 分上到 85 分。`4.3 / 4.4` 是锦上添花,不改也能跑,但跑的
过程会颠簸。

## 5. 当前结论

`DEVELOPMENT.md` 是一份**良好的执行计划**,不是一份糟糕的执行计划。
本评审的所有意见都是在 "已经成立" 的基础上做局部加固,**不否定主线**。

主要风险集中在两处:

1. Phase 4 颗粒过粗,会变成 "永远做不完的 Phase"
2. Phase 之间衔接机制缺失(状态、回归、下家),长期会产生信息断层

这两处建议优先改。其它意见(Phase 0 收窄 / Phase 3 hook 暂缓)对最终
质量影响小,可以根据时间情况选择性采纳。

如果不动文档,直接按当前 `DEVELOPMENT.md` 跑,主要副作用是:

- Phase 0 / Phase 1 同一批文件改两次(一周内的小重复)
- Phase 3 / Phase 4 hook 协议二次对齐(一周内的小重复)
- Phase 4 拖到无限期

前两个是**小成本可接受**,第三个是**真的问题**,至少 `4.5` 必须改。
