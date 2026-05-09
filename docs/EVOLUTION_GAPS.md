# Evolution Gaps

> 本文只做一件事:把 `ARCHITECTURE.md` 和 `EVOLUTION_PLAN.md` 当前的规划,
> 跟 Hermes / Voyager 这类真正能自主升级的 agent 系统对照一遍,
> 找出**还差什么结构性能力**。
>
> 本文不重复 `EVOLUTION_PLAN.md` 已经覆盖的内容,
> 只列出差距、影响、建议补的落点。

## 提纲

如果只想先抓重点,先看这一节。

### 这份文档怎么来

`EVOLUTION_PLAN.md` 已经规划了平台底座(prompt / context / memory /
tool_mgmt / provider_mgmt / agent_mgmt / artifact)和闭环骨架
(trace / reflection / skills / eval / governance),覆盖度已经很广。

但若把目标定在"agent 自主升级"——也就是不靠人持续介入,
agent 自己识别失败、形成假设、试错、验证、收敛新能力——
当前规划还缺几块**让闭环真正能转起来**的能力。

### 这份文档怎么读

- 想先看核心结论:看 `1. 当前判断`
- 想先看必须补的三件事:看 `2. P0 差距`
- 想先看可以并行展开的几件:看 `3. P1 差距`
- 想先看后期延伸:看 `4. P2 差距`
- 想看怎么塞进 `EVOLUTION_PLAN`:看 `5. 与 EVOLUTION_PLAN 的对应`

## 1. 当前判断

### 1.1 已经规划好的能力

按 `EVOLUTION_PLAN.md` 的 Milestone:

- 平台底座(A1 ~ A7):prompt / context / memory / tools / providers /
  agents / artifacts
- 闭环骨架(B1 ~ B5):trace / reflection / skills / eval / governance

这些是**承载自主升级的容器**。

### 1.2 还缺的结构性能力

如果只是补完 A1 ~ B5,系统会停在
**"人辅助调优 agent"**,不是
**"agent 自主升级"**。

差距按重要性分三档:

- P0(不补则闭环不成立):
  - `Objective system`(目标层)
  - `Sandbox / Shadow runtime`(隔离试错环境)
  - `Meta-agent / Self-driver`(自主调度闭环)
- P1(不补则闭环效率低):
  - `Hypothesis / Experiment` 对象
  - `Grader / Judge`(LLM 评判器)
  - `Failure taxonomy`(失败本体)
  - `Self-introspection tools`(自查工具集)
  - `Hard budget enforcement`(硬预算执行)
- P2(不补不影响 v1,但决定长期天花板):
  - `Curriculum`(自动出题)
  - `User feedback signal`(用户反馈信号)

下面逐项展开。每项按 `现象 / 缺口 / 建议补什么` 三段式。

## 2. P0 差距

### 2.1 Objective system(目标层)

**现象**

`B4 Evaluation` 规划了 eval cases / baseline compare / regression
detection,但**没有定义"什么算变好"**。
eval case 是 means(度量手段),objective 才是 ends(优化目标)。

**缺口**

- 没有 `chariot/objectives/` 模块
- 没有 `Objective` 对象(name / metric / target / horizon)
- 所有 reflection / update / eval 都没有"挂在哪个 objective 下"
- 后果:reflection 会发散成"什么都想改",update 没有 prioritization 依据

**建议补什么**

模块:

- `chariot/objectives/`

数据结构:

- `objectives`(id / name / metric_kind / target / horizon / status)
- `objective_metrics`(objective_id / metric_name / aggregator)
- `objective_results`(objective_id / window / value / source_eval_run)

接口契约:

- 每个 `eval_run` 必须 `objective_id`
- 每个 `skill_proposal` / `update_proposal` 必须声明
  `targets_objectives: [id]`
- `reflection` 输出必须挂在某个 objective 下,否则丢弃或归为
  `unscoped_observation`

CLI:

- `uv run chariot objective list`
- `uv run chariot objective show <id>`
- `uv run chariot objective progress <id>`

### 2.2 Sandbox / Shadow runtime(隔离试错环境)

**现象**

`B3 skill proposal` 提到了 `propose / install / rollback`,
`B5 governance` 提到了 `checkpoint / rollback`。

但**proposal 跑在哪没说**——是直接灌进主 runtime 跑 eval 吗?
那就已经污染了主 runtime / 主 DB / 主 FS,rollback 也只是事后补救。

**缺口**

- 没有"与主 runtime 同构但隔离的影子环境"
- 没有"在影子环境里跑 eval,通过后再 merge 进主"的路径
- generated tool 没有 sandbox validation 的物理基础
- 后果:checkpoint 只能保护已发生的写,不能阻止"试错本身的副作用"

**建议补什么**

模块:

- `chariot/sandbox/`(或 `shadow_runtime/`)

核心抽象:

- `ShadowRuntime`:fork 一份 AIAgent 实例 + 临时 DB + 临时 workspace
- `ShadowSession`:在 shadow runtime 里跑一次 chat / 一组 eval
- `ShadowDiff`:shadow 和主之间的状态差(用于 review / merge)

边界:

- shadow runtime **不能** 写主 DB
- shadow runtime **不能** 调真实写类工具(rm / write_file 默认 deny)
- shadow runtime **可以** 调 provider(烧 token,需走 budget)
- shadow runtime 的 trace 全部打 `shadow=true` 标记

依赖:

- 这是 `B3 / B4 / B5` 的**前置条件**,不是后续——
  当前 EVOLUTION_PLAN 把 sandbox 隐式藏进了 governance,需要显式提到 A 层

### 2.3 Meta-agent / Self-driver(自主调度闭环)

**现象**

A1 ~ B5 都规划好了,但**没人踩油门**。

- trace 是被动记录
- reflection 在 B2,但触发方式没说
- skill proposal 在 B3,触发方式也没说
- `chariot/cron/` 提了 "periodic curation / eval / cleanup",
  没提 "autonomous improvement loop"

**缺口**

- 没有"meta-agent / supervisor agent"概念
- 没有一个**专门负责跑 reflection → propose → eval → commit 这条 meta-loop**
  的 agent profile
- 后果:整套闭环只能靠人手动触发,
  "自主升级"不成立

**建议补什么**

模块:

- `chariot/meta_agent/`(或 `chariot/evolver/`)

核心抽象:

- `MetaAgent`:一种特殊的 `AIAgent` profile
  - 跑在 background(走 `agent_mgmt` 的 background execution)
  - 周期性触发(走 `cron`)或事件触发(trace 异常率超阈值)
  - toolset 限定为 meta-tools(见 §3.4 self-introspection)
- `EvolutionLoop`:meta-agent 的执行流水线
  ```
  扫 trace → 分类失败 → 形成 hypothesis →
  跑 sandbox experiment → 跑 eval →
  达标则 propose update → 走 governance approval →
  commit / rollback → 写回 memory
  ```

数据结构:

- `evolution_runs`(meta-agent 的一次完整跑)
- `evolution_decisions`(每次 propose / accept / reject 的记录)

边界:

- meta-agent 默认不能写主 runtime,只能 propose
- meta-agent 的 update 必须经 `B5 governance` 的 approval
  才能进主分支
- meta-agent 自己也要有 budget,见 §3.5

CLI:

- `uv run chariot evolver status`
- `uv run chariot evolver run --once`
- `uv run chariot evolver pause`

## 3. P1 差距

### 3.1 Hypothesis / Experiment 对象

**现象**

`B3` 直接从 reflection 跳到 skill proposal。
中间少了"假设"层,粒度太粗——不是所有改进都长成 skill。

**缺口**

- 没有 `hypothesis`(假设)对象
- 没有 `experiment`(实验)对象
- 后果:reflection 输出无法被结构化追踪,
  "改 prompt 段 A 是否会提升 task X 通过率"这种判断没有头

**建议补什么**

数据结构:

- `hypotheses`(id / objective_id / statement / proposed_change_kind /
  proposed_change_payload / status)
- `experiments`(id / hypothesis_id / sandbox_run_id / control_run_id /
  metric_delta / verdict)

边界:

- skill proposal 只是 hypothesis 通过 experiment 验证后的一种
  commit 形式;另外还可以是 prompt patch / tool policy change /
  context strategy change
- experiment 必须跑在 sandbox(见 §2.2)

### 3.2 Grader / Judge(LLM 评判器)

**现象**

B4 写 "baseline compare / regression detection",
但**怎么判**没说。两次输出都是自由文本,diff 看不出谁更好。

**缺口**

- 没有 `Grader` / `Judge` / `Rubric` 抽象
- eval case 自己塞判定逻辑,无法复用
- 后果:eval 只能跑确定性 case(exact match / unit test),
  跑不了开放式 case(写作 / 总结 / 分析)

**建议补什么**

模块:

- `chariot/eval/grader.py`

核心抽象:

- `BaseGrader`:抽象基类,接收 `(case, output) → score / verdict`
- `LLMGrader`:用另一个 provider 当 judge,带 rubric
- `RuleGrader`:正则 / exact match / unit test
- `Rubric`:打分规则(criteria / scale / examples)

边界:

- judge LLM 应该独立于被评测的 provider(避免自评偏差)
- judge 自己要被 calibrate——`eval_runs` 里要有人工标注样本做对照

### 3.3 Failure taxonomy(失败本体)

**现象**

`B1 trace platform` 只到 turn / provider / tool 三层,
没有"失败类型"维度。reflection 出来的都是
"这次不太对"这种没法行动的话。

**缺口**

- 没有"失败分类"
- 没有 failure_kind 维表
- 后果:reflection 无法对症下药——
  prompt 不清?tool 选错?参数错?context 不足?provider 抽风?

**建议补什么**

数据结构:

- `failure_kinds`(维表,初始覆盖):
  - `prompt_unclear`
  - `tool_selection_wrong`
  - `tool_arg_invalid`
  - `tool_execution_error`
  - `reasoning_error`
  - `context_insufficient`
  - `context_overflow`
  - `provider_error`
  - `output_format_violation`
- `trace_turns.classified_failure_kind`(可空)
- `trace_turns.classified_by`(`grader` / `human` / `meta_agent`)

边界:

- 分类由 grader / meta-agent 自动打标
- reflection 必须按 failure_kind 聚合,而不是按 turn 聚合

### 3.4 Self-introspection tools(自查工具集)

**现象**

CLI / sidecar 暴露了 `trace list / memory list / skill list`,
但这些是给**人**用的。

agent 自己升级自己,得先**能查询自己** ——
当前没有把这些 introspection 能力作为 tool 暴露给 agent。

**缺口**

- 没有 `chariot/tools/builtin/meta/` 工具集
- meta-agent 没有"查自己 / 改自己"的工具入口
- 后果:meta-agent 没法形成"我最近在哪类任务上失败最多"这类判断

**建议补什么**

模块:

- `chariot/tools/builtin/meta/`

工具(每个都是 `BaseTool` 子类):

- `query_my_traces`(by failure_kind / by tool / by date_range)
- `summarize_my_failures`(window)
- `list_my_skills`
- `list_my_objectives`
- `list_my_memories`(scope filter)
- `propose_skill_patch`(target_skill_id / patch / rationale)
- `propose_prompt_patch`(target_layer / patch / rationale)
- `request_experiment_run`(hypothesis_id)

边界:

- 这组工具默认**只对 meta-agent profile 开放**
- 普通 agent profile 默认 deny(走 `tool_mgmt` 的 profile 机制)
- 写类工具(`propose_*` / `request_experiment_run`)只产 proposal,
  不直接生效——必须走 governance

### 3.5 Hard budget enforcement(硬预算执行)

**现象**

B5 提了一句 "budget and quota",但没展开。

自主进化是**烧 token 大户**:reflection / experiment / sandbox eval
都要 LLM 调用。没有硬预算,一晚上能烧光。

**缺口**

- 没有 `budgets` 表
- 没有"调 provider 前查预算"的拦截器
- 后果:budget 只能事后审计,无法事前阻止

**建议补什么**

数据结构:

- `budgets`(scope_kind / scope_id / window / limit_tokens / limit_cost)
  - scope:`global` / `meta_agent` / `experiment` / `objective` / `conversation`
- `budget_usage`(budget_id / window_start / used_tokens / used_cost)

拦截点:

- `BaseProvider.generate` 入口前**进程内拦截器**(不靠 governance 后置)
- 超预算直接抛 `BudgetExceeded`,转
  `ChatEvent(kind="error", error_type="budget_exceeded")`

边界:

- meta-agent 必须有独立 budget,跟用户日常 chat 的 budget 解耦
- experiment 有 per-run budget,防止单次试错失控
- 全局 daily budget 是兜底

### 3.6 User feedback signal

**现象**

reflection 的输入只有 trace。但**用户的反馈**是最强信号:
显式 thumbs up/down,隐式"用户是否接受了输出 / 是否手动改了"。

**缺口**

- 没有 `feedback_events` 表
- UI 上没有反馈控件
- 后果:reflection 只能从 trace 自己推断好坏,丢掉了最直接的监督信号

**建议补什么**

数据结构:

- `feedback_events`(turn_id / kind / value / actor / created_at)
  - kind:`thumbs_up` / `thumbs_down` / `accepted` / `edited` /
    `regenerated` / `abandoned`

UI:

- 每条 assistant message 下方加 thumbs / regenerate 控件
- 用户改了下一条 prompt(而不是接续)算 implicit negative

边界:

- feedback 必须跟 turn / conversation / objective 关联
- reflection 必须把 feedback 作为一等输入,不能只看 trace

## 4. P2 差距

### 4.1 Curriculum(自动出题)

**现象**

eval 是 fixed cases。fixed cases 能防回归,
但**不能驱动新能力涌现**——agent 永远不会因为做完已有 case 就自动学会新东西。

Voyager 的关键洞察就是**让 agent 自己生成新挑战**。

**缺口**

- 没有 task generation 路径
- 没有 difficulty progression
- 后果:闭环只会让 agent "在已知任务上更稳",不会"覆盖更广任务"

**建议补什么**

模块:

- `chariot/curriculum/`

核心抽象:

- `ChallengeProposer`:基于 `已有 skills` + `失败模式` + `objectives`
  生成新 task
- `DifficultyEstimator`:估难度,做 progression
- 生成出来的 task 进 `eval_cases` pool,标 `source=auto`

边界:

- 自动生成的 case 默认隔离在 `experimental` suite,
  不混进 regression suite
- meta-agent 跑这些 case 验证"是否能解",
  能解的归为 mastered,不能解的形成新 hypothesis

### 4.2 (placeholder) 长期方向

下面这些不在 v1 范围,但若朝"长期持续运行"演进,
迟早要面对:

- **Distributed runner**:meta-agent 跑在远端,本地只是 viewer
- **Persistent agent identity**:跨时间保持"我是谁"的一致性,
  不靠每次重新组装 prompt
- **Cross-instance learning**:多用户/多实例之间的经验共享
  (隐私边界要先想清楚)
- **Multi-agent collaboration**:meta-agent 之间能协商
  (planner / critic / executor 分工)

这些都先记下,**v1 不做**。

## 5. 与 EVOLUTION_PLAN 的对应

下面给一个塞回 `EVOLUTION_PLAN.md` 的建议,不是硬性方案:

### 5.1 应该新增的 Milestone

- `Milestone A8: Objective system`
  - 放在 A 层尾部,因为 B1 ~ B5 都要引用 objective
- `Milestone A9: Sandbox / Shadow runtime`
  - 放在 A 层尾部,作为 B3 / B4 的物理基础
- `Milestone B0: Meta-agent / Self-driver`
  - 应该放在 B1 之前——meta-agent 是后续 B 层 milestone 的"驱动者",
    而不是被驱动的一环

调整后的 B 层顺序建议:

```
B0: Meta-agent / Self-driver       (新增,B 层入口)
B1: Trace platform                 (原 B1)
B2: Reflection and delegation      (原 B2,但 reflection 由 meta-agent 触发)
B3: Hypothesis / Experiment        (新增,夹在原 B2 和原 B3 之间)
B4: Skills and update loop         (原 B3)
B5: Evaluation and learning loop   (原 B4,要包含 grader / curriculum)
B6: Governance hardening           (原 B5,要包含 hard budget)
```

### 5.2 应该扩写的现有 Milestone

- `A6 Agent and task management`
  - 显式写出"meta-agent 是 agent profile 的一种"
  - background execution 必须支持 meta-agent 长期运行
- `B1 Trace platform`
  - 加 failure_kind 维度
  - 加 feedback_events 关联
- `B3 → B4 Skills and update loop`
  - 显式说明 skill 只是 update 的一种形式,
    其它包括 prompt patch / tool policy change / context strategy
- `B4 → B5 Evaluation and learning loop`
  - 加 Grader / Judge / Rubric 抽象
  - 加 curriculum / auto-generated case 通道
- `B5 → B6 Governance hardening`
  - hard budget 提到 P1
  - sandbox merge gate 显式作为 governance 的一种 verdict

### 5.3 应该补的工具集

- `chariot/tools/builtin/meta/`(见 §3.4)
- 默认只对 meta-agent profile 开放,
  在 `Milestone A4 tool management` 的 profile 机制里覆盖

## 6. 推进建议

### 6.1 不要等 B 层再补 P0

P0 三件事(objective / sandbox / meta-agent)看起来像 B 层职责,
但**它们是 B 层 milestone 的物理前提**。

如果先做完 B1 ~ B5,再回头补 P0,
B 层数据模型和接口会**第二次重写**。

建议时序:

```
A1 ~ A7  → 平台底座(已规划)
A8 ~ A9  → objective + sandbox(新增 P0 前两件)
B0       → meta-agent(新增 P0 第三件)
B1 ~ B6  → 原 B 层,但都挂在 P0 三件之上
```

### 6.2 P1 可以并行展开

P1 五件事彼此正交,能跟 B 层 milestone 并行落:

- Hypothesis / Experiment ↔ B4 (原 B3)
- Grader / Judge ↔ B5 (原 B4)
- Failure taxonomy ↔ B1
- Self-introspection tools ↔ B0
- Hard budget ↔ B6 (原 B5)

### 6.3 P2 留到 v2

P2 两件事(curriculum / feedback)对 v1 闭环不致命。
v1 闭环跑通后再决定 v2 是先做 curriculum 还是先做 distributed runner。

## 7. 当前结论

`EVOLUTION_PLAN.md` 的 A1 ~ B5 提供了**自主升级所需的容器**,
但不提供**让闭环转起来的发动机**。

发动机由三块组成:

1. `Objective` ——agent 知道朝哪个方向改
2. `Sandbox` ——agent 能安全地试错
3. `Meta-agent` ——不靠人也能持续触发"反思 → 试错 → 评估 → 提交"

补完这三件,加上 P1 五件配套(hypothesis / grader /
failure taxonomy / self-introspection / hard budget),
当前规划才能从"人辅助调优 agent"升级为"agent 自主升级"。

P2 两件(curriculum / feedback)决定长期天花板,不卡 v1。
