# 智能体自主进化(Phase B)开发方案

> 状态:草案,未实现,等用户拍板后分子 milestone 落地
> 参照:`D:\opendemo\claudedemo\phalanx\docs\agent-self-evolution.md` 八大技术点;phalanx 已落地的 §2.8.a-e 五段闭环
> 不在范围:Gateway IM 接入(跟自主进化正交,留 ROADMAP §v2 0.8.0);完全无人值守的 self-modifying code;权重级 RL 训练(B7 起步,需要前面所有数据)

## 0. "自主进化"的范围定义

只讨论以下 5 件事(phalanx 同款,不含 AGI 自我改写):

1. **跨 session 学习** — agent 记得过去发生了什么,下次不重蹈覆辙
2. **运行时反思** — 单 turn / 单 task 内 agent 评估自己的输出并修正
3. **能力固化** — 重复模式自动沉淀成 skill / tool / prompt template
4. **策略优化** — 基于历史 reward 自动调 prompt / 模型 / `reasoning_effort`
5. **权重级训练**(远期) — trajectory 数据回流做 SFT / RL

## 1. 自主进化闭环(共识图)

```
                ┌──────────────────────┐
   所有 turn  →│  经验流              │
                │  trajectory + memory │
                │  + sessions FTS      │
                └─────────┬────────────┘
                          │
                          ▼
                ┌──────────────────────┐
                │  反思 / 评估          │
                │  reflect + critic +  │
                │  golden task baseline│
                └─────────┬────────────┘
                          │
                          ▼
                ┌──────────────────────┐
                │  更新                 │
                │  prompt rewrite /    │
                │  skill creation /    │
                │  policy / weight     │
                └─────────┬────────────┘
                          │
                          ▼  governance 横切(checkpoint / approval / quota / audit)
                  回到经验流(在新 prompt / 新 skill 下继续运行)
```

**四段任一缺失就开环**:没经验流→反思无数据;没反思→经验只堆不蒸馏;没更新→反思产物落不到下次;**没评估→任何更新是猜测,可能让 agent 变差却没人知道**。

## 2. chariot 现状审计(对照 phalanx 八大技术点)

| 技术点 | 子能力 | chariot 状态 |
|---|---|---|
| **2.1 经验流** | 多轮对话持久化 | ✅ `conversations / messages`(0.4.0)|
| | trajectory(turn / tool / provider 完整记录)| ◇ 散在 `logs / audit_events / prompt_traces / context_traces`,缺 turn-level 树形关联 |
| | 长期记忆 cross-session | ✅ `memories / memory_events / memory_links` + `MemoryCaptureService`(0.7.0)|
| | FTS5 全文索引 | ✗ schema 已有 messages 表,缺 FTS 索引 + 检索函数 |
| | Context 自动压缩 | ✗ `ContextComposer` 是观察镜头,不做 trim / compress / summarize |
| | `@reference` 显式引用 | ✗ 未实现(`@file:` / `@diff:` / `@url:` / `@session:`)|
| **2.2 反思 / critic** | Reflect-then-retry | ✗ 未实现 |
| | Critic agent | ✗ 没有副 model 路径 |
| | Verification by execution | ✗ |
| **2.3 工具 / 技能** | 静态工具扩展 | ✅ `ToolRegistry` + `BaseTool`(0.4.0)|
| | Skills 系统(loader/registry) | ◇ `skills` 表 + `BaseSkill` ABC 存在,缺 activator / loader / 内置 sample |
| | Skill activator(prompt 注入 + 工具白名单) | ✗ |
| | Skill propose(agent 自发提议) | ✗ |
| | Curator(stale/underused/failing/overlapping) | ✗ |
| | 动态工具创建(codegen) | ✗ 高风险,远期 |
| **2.4 prompt / policy 调优** | Prompt 自动改写(DSPy / TextGrad) | ✗ |
| | Bandit 选模型 | ✗ |
| | CoT depth control | ✗ |
| **2.5 RL / 训练闭环** | Trajectory → SFT 数据 | ✗ |
| | Reward model | ✗ |
| | Atropos 接入 | ✗ |
| **2.6 评估闭环** | Golden task set + verifier | ✗ `eval_runs / eval_cases` 表存在但**没用起来**,没 YAML schema / verifier registry / runner |
| | Run 持久化 + diff vs baseline | ✗ |
| | CI smoke | ✗ |
| **2.7 安全网** | Tool guardrails(危险命令 regex) | ✗ `audit_events` 表存在但无 policy engine |
| | IterationBudget | ✅ `AgentLoop` 有 max_iterations 概念 |
| | Checkpoint / rollback | ◇ `checkpoints` 表存在,缺三件套(git stash + DB backup + config tarball)+ CLI |
| | Capability gating(`--enable-self-mod`) | ✗ |
| | Audit log(写性质操作可审计) | ◇ `audit_events` 表存在,缺自动 hook + CLI |
| **2.8 元控制** | Delegate / sub-agent | ✅ `TaskService.delegate` + 父子 task lineage(0.7.2)|
| | Plan-execute-verify | ✗ |
| | 多 agent debate | ✗ 研究方向 |

**关键 takeaway**:chariot 在**底座**(memory / prompt / context / provider / agent + task)已经成型,但**自主进化闭环零部件全部缺失**——评估、反思、安全网、skill 生命周期、golden task。这跟 phalanx 0.x 状态等价,phalanx 用了 §2.8.a-e 五段补完闭环。

## 3. 闭环差距优先级(ROI × 解锁能力 × 风险)

按 phalanx 推荐顺序映射到 chariot:

| 优先级 | Milestone | 解锁什么 | 阻塞什么 |
|---|---|---|---|
| **B1** | Trace platform | 一次 turn 完整事件流可重放 | 反思 / 评估 / curator / RL **全部** |
| **B2** | Evaluation loop(golden task) | "任何改动有数字" | 后续每条都靠 baseline 校准 |
| **B3** | Memory & Context 升级 | FTS5 + context compression + @reference | 反思的 context 质量 |
| **B4** | Reflection & Critic | critic agent / VERDICT 强制输出 | skill propose 的判断依据 |
| **B5** | Guardrails + Checkpoint + Audit | 自我修改红线 | skill / 动态工具创建的前置 |
| **B6** | Skills 生命周期 | propose / activator / curator | 真正的"能力固化" |
| **B7** | RL / Training(远期) | 权重级进化 | 等前 6 步数据齐 |

**最大瓶颈**:**评估闭环(B2)** —— 没基线衡量,后面 B4/B6 的任何"改进"都是猜测。但 B2 依赖 B1 的 trace 数据(trajectory + tool_calls + cost),所以**实际起点是 B1**。

## 4. B1 Trace platform 详细设计

### 4.1 设计意图

把"一次 turn 发生了什么"从 `logs / audit_events / prompt_traces / context_traces` 几张表里统一成 **trace 树**:turn → provider call(s) + tool call(s) + checkpoint(s)。

参考 phalanx `SessionDB.event_log`(schema_version 13):五类自动 hook(`tool_call_pre/post` / `guardrail_verdict` / `memory_store` / `checkpoint_create` / `rollback`),后续 audit / curator / RL 全部读这一张表。chariot 的设计要更进一步 —— 把"事件流"组织成"trace 树",每个 turn 是根。

### 4.2 数据模型

```sql
-- migration v17_trace_platform.sql

CREATE TABLE trace_turns (
    id TEXT PRIMARY KEY,                  -- ULID
    conversation_id TEXT,                 -- 关联 conversations.id(stateless 可为空)
    agent_profile TEXT,                   -- 关联 agent_profiles.name(可空)
    task_id TEXT,                         -- 关联 tasks.id(若 task 触发)
    task_run_id TEXT,                     -- 关联 task_runs.id
    provider_name TEXT NOT NULL,
    model TEXT,
    prompt_trace_id TEXT,                 -- 关联 prompt_traces.id(0.7.1 已有)
    context_trace_id TEXT,                -- 关联 context_traces.id(0.7.1 已有)
    status TEXT NOT NULL,                 -- running / completed / failed / cancelled
    stop_reason TEXT,                     -- end_turn / max_tokens / tool_use / cancelled / error
    error_type TEXT,
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd REAL,                        -- 累计成本(estimated / actual / unknown)
    cost_status TEXT,                     -- estimated / actual / included / unknown
    duration_ms INTEGER,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE trace_provider_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model TEXT,
    log_id TEXT,                          -- 关联 logs.id(wire 流水)
    request_summary TEXT NOT NULL,        -- JSON:{message_count, tool_count, max_tokens, ...}
    response_summary TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    latency_ms INTEGER,
    error_type TEXT,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE TABLE trace_tool_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    provider_call_id TEXT,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,              -- JSON
    result_summary TEXT,                  -- JSON 摘要(content 前 N 字 + is_error)
    duration_ms INTEGER,
    status TEXT NOT NULL,                 -- ok / error
    error_message TEXT,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE TABLE trace_checkpoints (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,                   -- before_tool / after_tool / before_apply / manual
    snapshot_id TEXT,                     -- 关联 checkpoints.id(0.7 已有)
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES trace_turns(id) ON DELETE CASCADE
);

CREATE INDEX idx_trace_turns_conversation ON trace_turns(conversation_id);
CREATE INDEX idx_trace_turns_task ON trace_turns(task_id);
CREATE INDEX idx_trace_turns_status ON trace_turns(status);
CREATE INDEX idx_trace_turns_started ON trace_turns(started_at DESC);
CREATE INDEX idx_trace_provider_calls_turn ON trace_provider_calls(turn_id);
CREATE INDEX idx_trace_tool_calls_turn ON trace_tool_calls(turn_id);
CREATE INDEX idx_trace_tool_calls_tool ON trace_tool_calls(tool_name);
CREATE INDEX idx_trace_checkpoints_turn ON trace_checkpoints(turn_id);
```

### 4.3 写入点

`AIAgent.run_chat` 主流程是唯一写入者(类比 `LogWriter`):

1. `run_chat` 入口:`trace_turns` 插入 status=running,拿 turn_id
2. `AgentLoop.stream_chat` 每轮 `provider.generate` 前后包计时 → `trace_provider_calls`
3. `AgentLoop._execute_tool` 每次工具调用前后包计时 → `trace_tool_calls`
4. `stream_chat` 结束:更新 `trace_turns` 的 status / finished_at / tokens / cost

**最小侵入**:新建 `chariot/trace/writer.py:TraceWriter`(无状态写入工具,封装 begin/end 配对)。AIAgent 持引用,所有写入收口。失败 log warning 不阻断主链路。

### 4.4 查询入口

- CLI:`chariot trace list [--conversation X | --task X | --provider X | --status X]`、`chariot trace view <turn-id>`(树形展开 turn → provider calls + tool calls + checkpoints)
- sidecar:`list_turns / get_turn / list_turn_provider_calls / list_turn_tool_calls`
- 桌面 UI:新 Trace 页(列表 + 详情;支持从 Conversation Detail 跳转)

### 4.5 B1 落地拆分(6 commit)

| 步 | 内容 |
|---|---|
| 1 | migration v17 + `models/trace.py` + `repos/trace_repo.py` + `services/trace.py` + 单测 |
| 2 | `chariot/trace/writer.py:TraceWriter`(begin/end 配对,best-effort)+ 单测 |
| 3 | sidecar surface(`TraceApi` + `TraceMethods` + 注册 RPC) |
| 4 | CLI `chariot trace {list, view, reconcile}` |
| 5 | AIAgent / AgentLoop 接入 TraceWriter,改最少代码 |
| 6 | 桌面 Trace 页 + 归档本设计文档 phase B1 |

## 5. B2 Evaluation loop 详细设计(golden task + verifier + diff)

### 5.1 设计意图

把"任何改动都有数字"装到 chariot 上 —— 单次跑全套 golden task → 落基线 → 改完代码 → diff 看回归。这是 phalanx §2.8.a 的直接移植 + chariot 风格化。

### 5.2 Golden Task schema(YAML)

```yaml
# tests/golden/file_read_pyproject.yaml
task_id: file_read_pyproject
prompt: "看一下 pyproject.toml 的内容,告诉我 name 字段"
verifier_type: tool_called
expected:
  tool: read_file
  args_subset:
    path: pyproject.toml
category: file
description: "agent 该用 read_file 工具拿 pyproject"
max_iterations: 10
model: null                    # null = 用 agent 默认 provider
system: null                   # null = 用 agent_profile / active bundle
```

### 5.3 三种 verifier

| Verifier | 输入 | 检查 |
|---|---|---|
| `exact_match` | `expected.contains: [str]`(AND) + `case_insensitive: bool` | `record.final_response` 必须全部包含 |
| `tool_called` | `expected.tool: str` + 可选 `args_subset: {k: v}`(strict equal) | `record.tool_calls` 至少一项 name+args 匹配 |
| `file_state` | `expected.path` + `exists` + `contains` | 跑完后文件状态符合 |

### 5.4 RunRecord 结构

```python
@dataclass
class RunRecord:
    task_id: str
    verdict: str                    # PASS / FAIL / ERROR / SKIP
    reason: str = ""
    turn_id: str                    # B1 关联!读 trace_turns 拿完整数据
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_status: str = "unknown"
    duration_seconds: float = 0.0
    final_response: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # 从 trace_tool_calls 拼
    error: str | None = None
```

### 5.5 关键解耦:Agent Factory

```python
AgentFactory = Callable[[GoldenTask], AIAgent]
```

CLI 注入真 `AIAgent`(调真 model);pytest CI 注入 `_StubAgent`(结构性测试,不调网络);未来 B6 注入"带 skill 预激活的 agent"做 ablation。

### 5.6 命令行入口

```powershell
uv run chariot eval                          # 跑全套
uv run chariot eval --category file          # 只跑某类
uv run chariot eval --task file_read_pyproject  # 单 task
uv run chariot eval list-tasks               # 列已有 task
uv run chariot eval list-runs                # 列历史 run(从 ~/.chariot/eval/<timestamp>/)
uv run chariot eval --baseline <run_id>      # 跟基线 diff
uv run chariot eval --no-save                # 不落盘
```

### 5.7 持久化

`~/.chariot/eval/<timestamp>/`:
- `records.json` — 全 RunRecord 列表
- `summary.json` — pass/fail/error/skip 计数 + 总 token / cost
- `tasks.json` — 当时跑的 task 快照(防 YAML 改动后 diff 漂移)
- `report.txt` — 人类可读

### 5.8 种子 task 集(10 个)

| 类别 | task_id | verifier | 检的是什么 |
|---|---|---|---|
| smoke | smoke_oneshot | exact_match | agent 跑通最小 loop |
| file | file_read_pyproject | tool_called + args | 调 `read_file` 且 path=pyproject.toml |
| file | file_list_dir_root | tool_called + args | 调 `list_dir` |
| file | file_write_artifact | file_state | 创建 `eval_artifact.txt` |
| web | web_get_example | tool_called + args | 调 `http_get` 且 url=https://example.com |
| plan | plan_explain_class | exact_match | 回复同时含 "class" 和 "method" |
| plan | plan_suggest_improvement | tool_called + args | 提建议前先读 README |
| multi | multi_search_then_read | tool_called | 隐式 search → read 链路 |
| memory | memory_recall_pref | exact_match | 引用之前对话里的偏好 |
| context | context_long_history_trim | exact_match | 长 history 下不丢关键信息 |

### 5.9 B2 落地拆分(5 wave,对齐 phalanx)

| Wave | 内容 |
|---|---|
| 1 | `tests/golden/` YAML schema + `chariot/eval/` 模块(GoldenTask / RunRecord / VerifierResult / Verdict + loader + runner skeleton + VERIFIERS registry + `chariot eval` argparse skeleton)|
| 2 | 10 个种子 golden task 文件 |
| 3 | 四种 verifier 实现(`exact_match` / `tool_called` / `file_state` / `output_schema`)+ `chariot/eval/cost.py`(从 trace_turns 拼成本)+ Runner 接 AIAgent + report 渲染 |
| 4 | run 持久化 `~/.chariot/eval/<timestamp>/` + `--baseline / --diff / --no-save` flag + CI smoke test |
| 5 | 桌面 Evals 页:tasks 列表 + runs grid(PASS/FAIL/ERROR/SKIP 配色)+ 单 run 详情 + baseline diff;task 行点击跳 Traces 页(走 turn_id 关联);sidecar `EvalApi` 暴露 `list_golden_tasks` / `list_eval_runs` / `get_eval_run` / `diff_runs` RPC |

## 6. B3 ~ B7 概要(具体设计留各自子文档)

### B3 Memory & Context 升级(补 phalanx §2.8.b)

- **FTS5 索引** — `messages` 表加 FTS5 virtual table,`memory_recall` 工具按语义查 / `chariot conversation search "..."`
- **Context 自动压缩** — `prompt_tokens > context_length × 0.7` 时 auxiliary LLM 把"最早 N turn"摘要成 `[context-summary]`,失败 fallback oldest-pair pruning
- **@reference 解析** — `@file:` / `@diff[:ref]` / `@url:` / `@session:<id|prefix>` 在 user 消息里展开为 `<reference type=... key=...>...</reference>` 块

**详细设计**:`docs/B3-memory-context-design.md`(wave 拆分 + 每 wave 的 schema / API / 接入点 / 安全限制)。

### B4 Reflection & Critic(补 phalanx §2.8.c)

- **AuxiliaryClient** — 副 model 路径,模型 / temperature / budget 跟主独立
- **Critic role** — `delegate_task --role critic` 派生子 agent,强制 `VERDICT: PASS|FAIL|UNSURE` 一行 + reason
- **Reflect-then-retry** — 工具失败 / verifier fail 时,主 agent 先调 critic 拿 critique,再重写计划
- **`chariot/reflection/`** 新模块

### B5 Guardrails + Checkpoint + Audit(补 phalanx §2.8.d)

- **Tool guardrails** — `chariot/guardrails/`:13 危险命令 regex(`rm -rf` / `chmod 777` / `git push --force` / DB drop / ...) + 三档 verdict(ALLOW / REQUIRE_APPROVAL / DENY) + 日配额
- **Checkpoint 三件套** — git stash + SQLite `Connection.backup()` + `~/.chariot/{config.yaml, .env}` tarball;CLI `chariot checkpoint {create, list, show, rollback, delete}`
- **Audit 自动 hook** — 五类:`tool_call_pre/post` / `guardrail_verdict` / `memory_store` / `checkpoint_create` / `rollback`;落 `audit_events` 表(已有)
- **Capability gating** — `--enable-self-mod` opt-in flag(默认关),`--yolo` 跳过所有审批

### B6 Skills 生命周期(对齐 phalanx §2.8.e)

- **Skill loader** — YAML manifest + `SkillRegistry`(扫 `chariot/skills/builtin/*.yaml`)+ 3 个内置 sample(`code_review` / `debug_helper` / `git_committer`)
- **Skill activator** — system prompt `<skill>` 块注入 + 工具白/黑名单双层过滤(schema 层 + dispatch 层)+ CLI `--skill`、REPL `/skill {list, <name>, clear}`
- **Skill propose** — `tools/builtin/propose_skill.py`:agent 自发在会话里提议固化,走完整链路:guardrail(self-mod 归类)→ enable_self_mod 检查 → 配额 → auto-checkpoint → audit `skill_create`
- **Curator** — `chariot/skills/curator.py`:4-bucket 静态评估(stale / underused / failing / overlapping),read-only,`chariot skill curate` CLI

### B7 RL / Training(远期)

- **Trajectory 导出** — `chariot eval export <run-id>` 把 trace_turns + trace_tool_calls + verdict 拼成 SFT / RL trajectory(对照 phalanx Atropos pipeline)
- **Reward signal** — `audit_events` 里 `skill_activate` + `tool_call_post.ok` + golden task `verdict=PASS` 是天然 reward
- **接 Atropos / Tinker** — 沙箱环境跑 PPO/GRPO,前置全部就绪

## 7. 推荐分支 / 版本策略

- 每个 milestone 独立 minor 版本:`feat/0.8.0-trace` / `feat/0.8.1-eval` / `feat/0.8.2-memory-context` / ...
- 当前分支 `feat/0.8.0-evolution` 是**总规划分支**,只放设计文档(本文 + 各 milestone 子设计草案),不实现代码
- 实际开发分别 cherry-pick / merge

**或者**(更轻量):
- 当前分支贯穿 B1,落地后归档设计 + merge 回 main;B2 起再开新分支

后者更接近 phalanx 实际工作流。倾向后者。

## 8. 安全前置红线(B5 起强制)

任何启用 "agent 改 chariot 自身" 的能力之前,下面三条必须满足(phalanx §2.8.d 同款):

1. **所有自我修改操作走 checkpoint** — write 前自动 snapshot,可一键 rollback
2. **Trajectory 永久 audit trail** — `audit_events` 表 + 五类自动 hook + CLI `chariot audit {log, count, show}`
3. **Capability gating 默认关闭** — `--enable-self-mod` opt-in + 日配额;`--yolo` 单独关掉所有审批,只用于明确的沙箱机器

B6 skill propose 是这套安全网的第一个考验:`propose_skill` 走 guardrail → 配额 → auto-checkpoint → audit 全链路,任何一环失败都 DENY。

## 9. 跨 Phase 风险

- **R1.** 5+ milestone 跨度大,建议**每个 minor 版本一个 milestone**(0.8.0 ~ 0.8.6),不要全塞 0.8.0
- **R2.** B2 evaluation 是其它所有 milestone 的"裁判",**必须在 B3-B6 任何改动前先有 baseline**;先 B1 trace + B2 eval,再做其它
- **R3.** Trace 数据增长快,B1 不做 retention(留 B5 加 policy);早期靠 DB 大小观察
- **R4.** B7 RL 训练接 Atropos / Tinker 是独立工程(对照 phalanx `tinker-atropos/` 子项目),不是 chariot 主仓库范围
- **R5.** phalanx 的 `@reference` 在 chariot 当前 ChatRequest 形态下需要 user message 预处理钩子(B3 会触及 `ChatRequest.messages` 解析),设计 B3 时再细化

## 10. 已拍板的决策(2026-05-11)

| # | 决策 | 选择 | 影响 |
|---|---|---|---|
| 1 | **B1 trace 数据粒度** | 选简单的:**只存摘要**(`request_summary` / `response_summary` JSON,`result_summary` 前 N 字)。不存完整 request / response payload | 磁盘可控;完整 wire payload 仍可通过 `logs` 表反查(`trace_provider_calls.log_id` 关联) |
| 2 | **B2 verifier 类型** | 起步 4 种:`exact_match` / `tool_called` / `file_state` / **`output_schema`**(检查 final_response 解析后符合 JSON Schema) | 多覆盖一类"结构化输出"任务 |
| 3 | **分支策略** | **全在 `feat/0.8.0-evolution`**,不拆每个 minor 分支 | 单一长 commit 链;落地完按 milestone 打 tag 归档 |
| 4 | **Skill 存储位置** | **phalanx 风格** `~/.chariot/skills/<name>/manifest.yaml` + 资源文件 — 用户可独立 git / 编辑 / 分发 | `skills` 表降级为 "活动状态 + 用量统计"索引层;manifest 是 source of truth |
| 5 | **Reflection 模块** | **参考 phalanx → 合并进 `chariot/agent/`**:`agent/auxiliary_client.py`(副 model 客户端)+ 走现有 `delegate` 派生 critic / planner role | 不开新顶层目录,reflection 是 agent 行为 |
| 6 | **audit_events 扩字段** | **参考 phalanx `event_log`** schema:加 `session_id` / `agent_id` / `target` / `content_hash` / 索引 `(event_type, created_at)` / `(session_id, created_at)`;原 `audit_events` 表名保留 | 跟 phalanx 兼容,后续 trajectory 导出 / RL reward 信号都从这一张表读 |

## 11. 落地启动方式

按 6 个决策定稿后:

1. 下一个动作 = 写 **`docs/B1-trace-design.md` 子设计文档**(参考 `tool-profile-design.md` 风格,~150 行,聚焦数据模型 + TraceWriter 接口 + 6 commit 拆分细化)
2. 然后是 **`docs/B2-eval-design.md`** —— 4 wave + 10 种子 task + 4 verifier(含 output_schema)详细
3. B1 / B2 子设计 review 完再开第一个 commit

或者跳过子设计直接动手 B1 phase 1(DB migration + domain),按一个 commit 一步推进。等明确 "开干 / 动手 / 执行 B1 第一步" 信号。
