# Evolution Plan

> 本文描述 `DEVELOPMENT.md` 完成之后，`chariot` 继续走向“智能体自主进化平台”时要做的详细开发计划。
> 这里不重复当前重构任务，而是只讲后续能力建设。
>
> 本文分成两层：
>
> 1. 平台底座扩展
> 2. 自主进化闭环

## 提纲

如果只想先抓重点，先看这一节。

### 这份计划的主线

后续开发不是直接做“自我进化”，而是分两步：

1. 先把平台底座补齐
2. 再把自主进化闭环接起来

### 平台底座要补什么

1. `Prompt system（提示词系统）`
2. `Context management（上下文管理）`
3. `Memory platform（长期记忆平台）`
4. `Tool management（工具管理）`
5. `Provider management（模型提供方管理）`
6. `Agent and task management（智能体与任务管理）`
7. `Artifact management（结果产物管理）`

### 自主进化闭环要补什么

1. `Trace platform（执行轨迹平台）`
2. `Reflection and delegation（反思与任务委派）`
3. `Skills and update loop（技能与更新闭环）`
4. `Evaluation and learning loop（评估与学习闭环）`
5. `Governance hardening（治理机制加固）`

### 如果只想先看重点，推荐阅读顺序

- 想知道后面先做什么：看 `2. 执行顺序`
- 想知道平台底座缺什么：看 `3 ~ 9`
  这里主要是 `prompt（提示词）`、`context（上下文）`、`memory（长期记忆）`、`tools（工具）`、`providers（模型提供方）`
- 想知道真正的自主进化部分：看 `10 ~ 14`
  这里主要是 `trace（执行轨迹）`、`reflection（反思）`、`skills（技能固化）`、`evaluation（评估）`、`governance（治理）`
- 想知道最后做到什么程度才算闭环：看 `15. 最终闭环标准`

## 1. 前提

默认前提是：

- `ARCHITECTURE.md` 里的主链路已稳定
- `DEVELOPMENT.md` 里的 `Phase 0 ~ Phase 5` 已完成
- 当前目录结构继续保持不变

## 2. 执行顺序

建议按下面顺序推进：

### 2.1 平台底座扩展

1. Milestone A1: Prompt system
2. Milestone A2: Context management
3. Milestone A3: Memory platform
4. Milestone A4: Tool management
5. Milestone A5: Provider management
6. Milestone A6: Agent and task management
7. Milestone A7: Artifact management

### 2.2 自主进化闭环

8. Milestone B1: Trace platform
9. Milestone B2: Reflection and delegation
10. Milestone B3: Skills and update loop
11. Milestone B4: Evaluation and learning loop
12. Milestone B5: Governance hardening

原因：

- `prompt / context / memory / tools / providers / agents / artifacts` 是底座。
- `trace -> reflection -> update -> evaluation -> governance` 是跑在底座上的闭环。
- 如果底座不清楚，闭环会落到到处都是 hook 和临时逻辑。

## 3. Milestone A1: Prompt system

### 3.1 目标

把当前 prompt 组装逻辑从“零散拼接”提升成明确的 `prompt system`。

### 3.2 具体功能

#### 3.2.1 Prompt layers

支持这些层次：

- base system prompt
- developer prompt
- runtime prompt
- memory prompt
- skill prompt
- tool instruction prompt

#### 3.2.2 Prompt build pipeline

统一 prompt 组装顺序：

1. base system
2. developer layer
3. runtime mode layer
4. memory injection
5. skill injection
6. tool instruction / schema injection

#### 3.2.3 Prompt compaction

支持：

- trim
- compress
- summarize
- collapse repeated sections

#### 3.2.4 Prompt trace

记录：

- final prompt size
- injected sections
- prompt version
- prompt source references

### 3.3 模块落点

- `chariot/prompt/`
- `chariot/agent/`
- `chariot/repos/`

### 3.4 数据结构

建议新增：

- `prompt_bundles`
- `prompt_versions`
- `prompt_traces`

### 3.5 对外入口

CLI：

- `uv run chariot prompt list`
- `uv run chariot prompt show <id>`
- `uv run chariot prompt inspect <turn-id>`

sidecar：

- `list_prompt_bundles`
- `get_prompt_bundle`
- `inspect_prompt`

UI：

- Prompt bundle page
- Prompt inspect drawer

### 3.6 手动验收 demo

```powershell
uv run chariot chat --convo new "hello"
uv run chariot prompt inspect <turn-id>
uv run chariot prompt list
```

### 3.7 验收要求

- 每次 turn 都能看到最终 prompt 由哪些层组成。
- memory / skill / tool 注入顺序稳定。
- prompt 长度控制能生效。

## 4. Milestone A2: Context management

### 4.1 目标

把“本轮最终拿到哪些上下文”从隐式逻辑提升成独立子系统。

### 4.2 具体功能

#### 4.2.1 Context sources

支持：

- message history
- pinned memory
- relevant memory
- skill references
- workspace files
- prior traces
- artifacts

#### 4.2.2 Context ranking

按这些维度排序：

- relevance
- recency
- scope match
- pinned priority

#### 4.2.3 Context compression

支持：

- long history trim
- artifact summary
- repeated context dedupe

#### 4.2.4 Context reference

支持引用而非全文注入：

- artifact id
- trace id
- memory id
- skill id

### 4.3 模块落点

- `chariot/context/`
- `chariot/agent/`
- `chariot/repos/`

### 4.4 数据结构

建议新增：

- `context_snapshots`
- `context_sources`

### 4.5 对外入口

CLI：

- `uv run chariot context inspect <turn-id>`
- `uv run chariot context sources <turn-id>`

sidecar：

- `inspect_context`

UI：

- Context panel
- Source list drawer

### 4.6 手动验收 demo

```powershell
uv run chariot chat --convo new "总结一下当前项目结构"
uv run chariot context inspect <turn-id>
uv run chariot context sources <turn-id>
```

### 4.7 验收要求

- 每次 turn 能解释“为什么拿到了这些上下文”。
- context 过长时能被压缩，而不是直接溢出。

## 5. Milestone A3: Memory platform

### 5.1 目标

把 `memory` 做成真正可写入、可检索、可注入、可维护的长期记忆系统。

### 5.2 具体功能

#### 5.2.1 Explicit memory CRUD

支持：

- `preference`
- `project_fact`
- `instruction`
- `lesson`

#### 5.2.2 Automatic memory capture

从 trace 和 conversation 自动提取：

- 稳定用户偏好
- 项目事实
- 常见失败模式
- 修复经验

#### 5.2.3 Retrieval policy

按这些维度检索：

- `workspace`
- `convo`
- `provider`
- `tags`
- `recency`
- `pinned`

#### 5.2.4 Injection policy

控制：

- `max_items`
- `max_chars`
- `priority_order`
- `pinned_first`

#### 5.2.5 Consolidation

支持：

- merge duplicates
- archive stale items
- summarize similar items

### 5.3 模块落点

- `chariot/memory/`
- `chariot/agent/`
- `chariot/repos/`
- `chariot/cli/commands/`
- `chariot/sidecar/methods/`

### 5.4 数据结构

建议新增：

- `memories`
- `memory_events`
- `memory_links`

### 5.5 对外入口

CLI：

- `uv run chariot memory list`
- `uv run chariot memory add --type preference --text "..."`
- `uv run chariot memory show <id>`
- `uv run chariot memory delete <id>`

sidecar：

- `list_memories`
- `create_memory`
- `delete_memory`

UI：

- Memory list page
- Memory detail drawer
- Pin / archive actions

### 5.6 手动验收 demo

```powershell
uv run chariot memory add --type preference --text "默认用中文输出，但保留关键 English technical terms"
uv run chariot memory list
uv run chariot chat --convo new "以后回答我用什么语言风格？"
uv run chariot memory show <id>
```

### 5.7 验收要求

- memory 能查到、能删除、能注入。
- 新会话里能体现 memory 注入效果。
- memory 注入有上限控制。

## 6. Milestone A4: Tool management

### 6.1 目标

把工具系统从“能调用”提升成“可治理、可配置、可版本化”的 `tool management` 平台。

### 6.2 具体功能

#### 6.2.1 Tool registry and config

支持：

- register
- list
- enable / disable
- view config
- update config

#### 6.2.2 Tool profiles

支持给不同 agent / role / workspace 绑定不同 toolset。

#### 6.2.3 Tool policy

支持：

- allow
- require approval
- deny

按这些维度判断：

- tool name
- argument pattern
- workspace path
- runtime mode

#### 6.2.4 Tool audit

记录：

- who called
- input summary
- verdict
- result summary
- duration

#### 6.2.5 Generated tool lifecycle

支持：

- proposal
- sandbox validation
- review
- install
- rollback

### 6.3 模块落点

- `chariot/tool_mgmt/`
- `chariot/tools/`
- `chariot/repos/`

### 6.4 数据结构

建议新增：

- `tool_profiles`
- `tool_policies`
- `tool_versions`
- `generated_tools`

### 6.5 对外入口

CLI：

- `uv run chariot tool list`
- `uv run chariot tool show <name>`
- `uv run chariot tool enable <name>`
- `uv run chariot tool disable <name>`
- `uv run chariot tool profile list`

sidecar：

- `list_tools`
- `get_tool`
- `update_tool_policy`

UI：

- Tool library
- Tool policy page
- Tool profile page

### 6.6 手动验收 demo

```powershell
uv run chariot tool list
uv run chariot tool enable list_dir
uv run chariot tool show list_dir
uv run chariot tool profile list
```

### 6.7 验收要求

- tool 可启用、停用、查看策略。
- 同一工具在不同 profile 下可表现不同授权范围。
- 每次调用会留下审计记录。

## 7. Milestone A5: Provider management

### 7.1 目标

把 provider 从“配置项”提升成“可路由、可回退、可监控”的 provider 平台。

### 7.2 具体功能

#### 7.2.1 Provider profile

支持：

- default model
- capability flags
- retry policy
- timeout policy

#### 7.2.2 Model routing

支持：

- per task routing
- per role routing
- per workspace routing

#### 7.2.3 Fallback

支持：

- same provider fallback
- cross provider fallback

#### 7.2.4 Health and cost tracking

记录：

- health status
- latency
- token usage
- estimated cost

### 7.3 模块落点

- `chariot/provider_mgmt/`
- `chariot/providers/`
- `chariot/repos/`

### 7.4 数据结构

建议新增：

- `provider_profiles`
- `provider_health`
- `provider_cost_events`

### 7.5 对外入口

CLI：

- `uv run chariot provider list`
- `uv run chariot provider show <name>`
- `uv run chariot provider probe <name>`
- `uv run chariot provider status`

sidecar：

- `list_providers`
- `probe_provider`
- `get_provider_status`

UI：

- Provider dashboard
- Status page

### 7.6 手动验收 demo

```powershell
uv run chariot provider list
uv run chariot provider probe mock
uv run chariot provider status
```

### 7.7 验收要求

- provider 能看到 profile、health、capabilities。
- provider 配置修改后能热生效。

## 8. Milestone A6: Agent and task management

### 8.1 目标

给后续 delegation、多角色、后台任务提供正式的 agent / task 基础设施。

### 8.2 具体功能

#### 8.2.1 Agent profile

支持：

- role
- prompt bundle
- tool profile
- provider profile
- budget

#### 8.2.2 Task object

支持：

- task id
- task goal
- status
- parent / child
- artifacts

#### 8.2.3 Background execution

支持：

- queue
- run
- pause
- resume
- cancel

#### 8.2.4 Scheduled jobs

支持：

- periodic curation
- periodic eval
- periodic cleanup

### 8.3 模块落点

- `chariot/agent_mgmt/`
- `chariot/delegation/`
- `chariot/cron/`
- `chariot/repos/`

### 8.4 数据结构

建议新增：

- `agent_profiles`
- `tasks`
- `task_runs`
- `scheduled_jobs`

### 8.5 对外入口

CLI：

- `uv run chariot agent list`
- `uv run chariot task list`
- `uv run chariot task show <id>`
- `uv run chariot job list`

sidecar：

- `list_agents`
- `list_tasks`
- `get_task`

UI：

- Task board
- Background jobs page

### 8.6 手动验收 demo

```powershell
uv run chariot task list
uv run chariot job list
```

### 8.7 验收要求

- agent / task / job 有明确对象模型。
- 后台任务状态可查询，不再只是临时执行。

## 9. Milestone A7: Artifact management

### 9.1 目标

把 trace、eval、skill proposal、checkpoint、dataset export 等中间产物收成统一的 artifact 管理系统。

### 9.2 具体功能

#### 9.2.1 Artifact catalog

支持：

- list
- show
- filter by type
- filter by owner

#### 9.2.2 Artifact storage

支持：

- local path registry
- checksum
- version
- retention policy

#### 9.2.3 Artifact linking

支持关联到：

- turn
- task
- skill proposal
- eval run
- checkpoint

### 9.3 模块落点

- `chariot/artifact_mgmt/`
- `chariot/repos/`

### 9.4 数据结构

建议新增：

- `artifacts`
- `artifact_links`

### 9.5 对外入口

CLI：

- `uv run chariot artifact list`
- `uv run chariot artifact view <id>`

sidecar：

- `list_artifacts`
- `view_artifact`

UI：

- Artifact browser

### 9.6 手动验收 demo

```powershell
uv run chariot artifact list
uv run chariot artifact view <id>
```

### 9.7 验收要求

- 主要中间产物都能统一查到。
- artifact 可以追溯到来源任务或 turn。

## 10. Milestone B1: Trace platform

### 10.1 目标

把一次 turn、一次 provider 调用、一次 tool 调用、一次 checkpoint 都串起来查询。

### 10.2 具体功能

- turn trace
- provider trace
- tool trace
- checkpoint trace
- trace query by convo / tool / provider / error

### 10.3 模块落点

- `chariot/audit/`
- `chariot/agent/`
- `chariot/providers/`
- `chariot/tools/`
- `chariot/repos/`

### 10.4 数据结构

- `trace_turns`
- `trace_provider_calls`
- `trace_tool_calls`
- `trace_checkpoints`

### 10.5 对外入口

CLI：

- `uv run chariot trace list`
- `uv run chariot trace view <turn-id>`

### 10.6 手动验收 demo

```powershell
uv run chariot chat --convo new "hello"
uv run chariot trace list
uv run chariot trace view <turn-id>
```

### 10.7 验收要求

- 一次 chat 后至少能查到 turn / provider / tool 三层 trace。

## 11. Milestone B2: Reflection and delegation

### 11.1 目标

让 agent 具备任务拆分、子 agent 调用、自我复盘能力。

### 11.2 具体功能

- `delegate_task`
- role presets
- reflection pass
- background delegation
- lineage tracking

### 11.3 模块落点

- `chariot/delegation/`
- `chariot/agent_mgmt/`
- `chariot/tools/`

### 11.4 数据结构

- `delegate_tasks`
- `delegate_task_runs`
- `delegate_lineage`
- `reflection_notes`

### 11.5 对外入口

CLI：

- `uv run chariot delegate list`
- `uv run chariot delegate start --role planner --prompt "..."`

### 11.6 手动验收 demo

```powershell
uv run chariot delegate start --role planner --prompt "把实现 trace page 的任务拆成 3 个子任务"
uv run chariot delegate list
```

### 11.7 验收要求

- 子任务状态可查询。
- reflection 结果能被记录。

## 12. Milestone B3: Skills and update loop

### 12.1 目标

把经验固化成可复用 skill，并让 skill 进入受控更新循环。

### 12.2 具体功能

- skill packaging
- skill registry
- skill activation
- propose skill
- update / rollback / archive

### 12.3 模块落点

- `chariot/skills/`
- `chariot/agent/`
- `chariot/repos/`

### 12.4 数据结构

- `skills`
- `skill_versions`
- `skill_usage`
- `skill_proposals`

### 12.5 对外入口

CLI：

- `uv run chariot skill list`
- `uv run chariot skill propose --from-trace <turn-id>`

### 12.6 手动验收 demo

```powershell
uv run chariot skill list
uv run chariot skill propose --from-trace <turn-id>
```

### 12.7 验收要求

- skill 可启用、停用、提案、回滚。

## 13. Milestone B4: Evaluation and learning loop

### 13.1 目标

建立“变更前后是否真的更好”的判断机制，并支持导出学习数据。

### 13.2 具体功能

- eval cases
- eval suite run
- baseline compare
- regression report
- failed / successful trajectory export
- dataset packaging

### 13.3 模块落点

- `chariot/eval/`
- `chariot/repos/`
- `chariot/artifact_mgmt/`

### 13.4 数据结构

- `eval_runs`
- `eval_cases`
- `eval_results`
- `eval_artifacts`

### 13.5 对外入口

CLI：

- `uv run chariot eval run smoke`
- `uv run chariot eval diff <run-a> <run-b>`
- `uv run chariot eval export <run-id>`

### 13.6 手动验收 demo

```powershell
uv run chariot eval run smoke
uv run chariot eval list
uv run chariot eval export <run-id>
```

### 13.7 验收要求

- 至少有一组稳定 smoke suite。
- eval 结果可导出为训练数据。

## 14. Milestone B5: Governance hardening

### 14.1 目标

把治理骨架做成真正生效的安全边界。

### 14.2 具体功能

- policy engine
- approval workflow
- checkpoint and rollback
- budget and quota
- security review

### 14.3 模块落点

- `chariot/guardrails/`
- `chariot/checkpoints/`
- `chariot/audit/`

### 14.4 数据结构

- `policy_verdicts`
- `approval_requests`
- `approval_decisions`
- `checkpoint_records`
- `quota_events`

### 14.5 对外入口

CLI：

- `uv run chariot approval list`
- `uv run chariot approval approve <id>`
- `uv run chariot checkpoint list`

### 14.6 手动验收 demo

```powershell
uv run chariot approval list
uv run chariot checkpoint list
```

### 14.7 验收要求

- 高风险 update 默认不能直接生效。
- approval / checkpoint / rollback 有完整记录。

## 15. 最终闭环标准

当下面这些条件同时成立时，才可以认为 `chariot` 进入了可用的自主进化阶段：

1. prompt、context、memory、tool management、provider management、agent management、artifact management 都已成型。
2. 每次 turn 都能留下完整 trace。
3. agent 能通过 reflection 和 delegation 形成改进建议。
4. 改进建议能形成 skill 或其他受控 update。
5. 每次 update 都能通过 eval 判断是否真的更好。
6. 整个过程受 governance 控制，并可 checkpoint / rollback。

## 16. 当前结论

后续开发不应该只盯着“怎么做自我改进”，还要先补齐承载这些改进的平台底座。

也就是说：

- `ARCHITECTURE.md` 解决“系统应该长什么样”
- `DEVELOPMENT.md` 解决“当前重构怎么做”
- `EVOLUTION_PLAN.md` 解决“重构之后，怎么把平台真正推进到自主进化”
