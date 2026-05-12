# B7 — RL 数据 pipeline

> Milestone:`feat/0.8.6-rl`
> 范围:把 chariot 跑出来的 conversation / turn 转成 RL 训练数据集 ——
> trajectory export + reward annotator(静态规则 + critic 二次评分两层合一)+
> dataset packaging + golden eval suite。**B7 不做真正的 fine-tune / DPO / RLHF**
> ——那需要 GPU / 大模型 weights / 训练框架,单 milestone 装不下,留 B8+。
> 设计前提:
> - v22 `audit_events` / `trace_turns` / `trace_tool_calls` / `messages` 表已落
> - B4 critic 副 LLM(`auxiliary_clients.name='critic'`)装上即可复用,不必新加
> - B5 `guardrail_verdict` / B6 `skill_activate` audit events 已经在写;B7 只
>   消费,不改 upstream
> - dataset 格式跟主流 trl / verl / unsloth 对齐(`{"prompt", "response",
>   "reward", "metadata"}` JSONL),用户自带 trainer 消费
> 模块归属:`chariot/rl/` 顶层独立子包,跟 `chariot/audit/` / `chariot/checkpoints/`
> / `chariot/skills/` 同级

## 安全前置红线(继承 B5 §8)

- **导出含敏感信息**:`messages.content` 可能含 API key / 私聊上下文。export
  默认走 `secret_scrubber`(扫常见 token / key / email pattern 替换 `<REDACTED>`),
  可 `--raw` 关掉(打 banner 警告)
- **数据落 disk 不写 DB**:trajectory / dataset 是 `~/.chariot/rl/` 子目录的
  JSONL 文件,**不**进 sqlite —— 训练数据可能巨大,数据库不该承担
- **B5 audit**:每次 `chariot rl export` / `chariot rl pack` 写一条
  `audit_events.rl_export`(B7 新事件类型),payload 含输出路径 / 行数 / scrub 模式
- **golden eval 不会改 DB**:跑 golden tasks 走独立 `--eval-db` 临时 sqlite,
  防止 eval 污染用户实际 conversation 库

## Wave 拆分

| Wave | 内容 |
|---|---|
| 1 | Trajectory exporter — `chariot/rl/exporter.py`:`TrajectoryExporter` 类,从 conversation_id 提取 (messages + tool_calls + provider_calls + audit_events) → JSONL;字段对齐 trl SFT 格式;`secret_scrubber.py`(`SecretScrubber` 正则集合);CLI `chariot rl export <conv-id> [--out path] [--raw]` + sidecar `export_trajectory` RPC + tests |
| 2 | Reward annotator — `chariot/rl/reward.py`:`RewardAnnotator` 类两层合一(`StaticReward` 静态规则 + `CriticReward` 走 B4 CriticAgent 0-1 评分),加权和(权重 ClassVar,缺 critic 退化纯静态);`chariot rl annotate <jsonl-in> --out <jsonl-out>`;CLI 跑批 + tests |
| 3 | Dataset packager + Golden eval — `chariot/rl/packager.py` 把多条 trajectory 打成单一 dataset JSONL(去重 + reward 分桶 + train/eval split);`chariot/rl/golden.py` 装 N 条 golden conversation goal,跑 chariot agent 产 trajectory 再 annotate;`chariot rl golden run` / `chariot rl pack <dir>` + tests |
| 4 | 桌面 `/rl` 页(三 tab:Trajectories / Reward / Dataset)+ demo doc §7.18 收尾 + LONGTERMPLAN.md "B8 真正训练" 占位 |

---

## Wave 1 详细设计:Trajectory exporter

### 模块布局

```
chariot/rl/
├── __init__.py                # re-export TrajectoryExporter / ExportEntry / RewardAnnotator / ...
├── base.py                    # frozen dataclass:ExportEntry / RewardScore / DatasetEntry
├── exporter.py                # TrajectoryExporter(主路径)
├── scrubber.py                # SecretScrubber(正则集合 + apply)
├── reward.py                  # wave 2:RewardAnnotator + StaticReward + CriticReward
├── packager.py                # wave 3:DatasetPackager
└── golden.py                  # wave 3:GoldenEvalRunner + 3 内置 goal
```

### `ExportEntry` 数据形态

```python
@dataclass(frozen=True)
class ExportEntry:
    """一次 chat turn 的完整 trace。dataset JSONL 一行一个 ExportEntry。"""

    # 主键
    conversation_id: str
    turn_id: str               # trace_turns.id
    sequence: int              # 同一 conversation 内的 turn 序号(从 0)

    # 模型输入 / 输出(SFT 格式)
    prompt: list[dict]         # role/content blocks(scrubbed)
    response: list[dict]       # assistant block list(text + tool_use,scrubbed)

    # 副产物
    tool_calls: list[dict]     # 每个 tool_call:{name, args, result_snippet, is_error}
    provider_call: dict        # {provider, model, latency_ms, usage}
    audit_signals: dict        # {guardrail_hits, skill_activations, reflection_verdict, ...}

    # B7 wave 2 起填(wave 1 留空)
    reward: float | None
    reward_breakdown: dict[str, float] | None

    # 元数据
    agent_profile: str | None
    stop_reason: str | None
    error_type: str | None
    started_at: str            # ISO8601
    duration_ms: int | None
```

### `TrajectoryExporter`

```python
class TrajectoryExporter:
    """从 DB 拼出一条 conversation 的完整 trajectory。"""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        scrubber: SecretScrubber | None = None,
    ) -> None: ...

    async def export(self, conversation_id: str) -> list[ExportEntry]:
        """按 trace_turns.started_at 升序返;每个 turn 一条 ExportEntry。"""

    async def export_to_jsonl(self, conversation_id: str, out: Path) -> int:
        """写 JSONL 文件,返行数;路径不存在父目录会自动 mkdir。"""
```

实现要点:
- 读 `trace_turns` filter `conversation_id` → 按 `started_at` asc 排
- 每个 turn 关联读 `trace_provider_calls`(取 1 条,最近的)+ `trace_tool_calls`
  (按 started_at asc 多条)
- `messages` 表按 turn 切分(turn.started_at <= message.created_at < next_turn.started_at)
- `audit_events` filter created_at 在 turn 窗口内,按 event_type 分桶
  (guardrail_hits / skill_activations / reflection / memory_store / tool_call_*)
- 走 `secret_scrubber.scrub(content)` 过一遍 messages / tool args / results

### `SecretScrubber`

```python
class SecretScrubber:
    """敏感串扫除。正则集合 ClassVar;`scrub(text) -> text` 替换为 `<REDACTED>`。

    模式集合(初版,后续按用户反馈补):
    - api_key:`sk-[A-Za-z0-9]{20,}` / `xoxb-[A-Za-z0-9-]{20,}`
    - aws_access:`AKIA[A-Z0-9]{16}` / `(?i)aws_secret[_ ]*key.{0,5}[=:].{20,}`
    - bearer:`(?i)bearer\s+[A-Za-z0-9._\-]{20,}`
    - email:可选(`--scrub-email` 才开,默认保留;邮箱在 trajectory 里常有用)
    - private_key:`-----BEGIN [A-Z ]*PRIVATE KEY-----`
    """

    PATTERNS: ClassVar[list[tuple[str, re.Pattern[str]]]]

    def scrub(self, text: str) -> str: ...
    def scrub_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
```

`--raw` flag 跳过 scrubber(走 `NullScrubber`,做 no-op),用于本地训练自用
+ banner 警告。

### Surface(wave 1)

- CLI:
  - `chariot rl export <conversation_id> [--out path.jsonl] [--raw]`
  - `--out` 默认 `~/.chariot/rl/trajectories/<conv-id>.jsonl`
- sidecar:`export_trajectory({conversation_id, raw?: bool})` → 返
  `{out_path, row_count, scrub_mode}`
- 桌面:wave 4 才接

### v23 migration —— rl_export audit 事件

不加新表,但 `audit_events.event_type` 加新枚举 `rl_export`(payload 含
`out_path / row_count / conversation_id / scrub_mode`)。`AuditHookManager`
加 `EVENT_RL_EXPORT` ClassVar + `record_rl_export` helper。

> 不动 schema,只是新 event_type 字符串。所以 **没有 v23 migration sql**。

### 测试(wave 1)

- `tests/platform/test_trajectory_exporter.py`:
  - 单 conversation 多 turn → 行数 / 字段完整性
  - 含 tool_call 的 turn → tool_calls 列表非空
  - 含 guardrail hit / skill_activate 的 turn → audit_signals 字段填上
  - 空 conversation → 返空 list
- `tests/platform/test_secret_scrubber.py`:
  - sk-key / AKIA / bearer / private_key 都打 REDACTED
  - 普通文本不动
  - blocks(list of dict)递归 scrub text 字段
- `tests/sidecar/test_rl_methods.py`:`export_trajectory` 返码 + 文件存在性

---

## Wave 2 详细设计:Reward annotator

### `RewardAnnotator` 两层合一

```python
class RewardAnnotator:
    """静态规则 + critic 评分加权合一。

    用法::

        annotator = RewardAnnotator(
            static_reward=StaticReward(),
            critic_reward=CriticReward.from_agent(agent),  # None 退化纯静态
            weights={"static": 0.6, "critic": 0.4},
        )
        scored = await annotator.score(entry)  # ExportEntry → RewardScore

    """

    async def score(self, entry: ExportEntry) -> RewardScore: ...
    async def annotate_jsonl(self, in_path: Path, out_path: Path) -> int: ...
```

`RewardScore` frozen dataclass:

```python
@dataclass(frozen=True)
class RewardScore:
    reward: float              # 加权合成最终值 [-1.0, +1.0]
    static_part: float         # 静态规则部分
    critic_part: float | None  # critic 部分(无 critic agent → None)
    breakdown: dict[str, float]  # 每条 sub-rule 的贡献(debug 用)
```

### `StaticReward`

每条 ExportEntry → `[-1.0, +1.0]` 之间的得分。规则集合(初版,后续可调权重):

| 信号 | 来源 | 加分 / 扣分 |
|---|---|---|
| reflection verdict PASS | `audit_signals.reflection_verdict` | +0.4 |
| reflection verdict FAIL | 同上 | -0.4 |
| guardrail DENY 命中 | `audit_signals.guardrail_hits` 中 verdict=deny 条数 | -0.3 / 条(上限 -0.6) |
| guardrail REQUIRE_APPROVAL 命中 | 同上 verdict=require_approval | -0.1 / 条 |
| tool_call is_error 比例 | `tool_calls` 内 `is_error=true` / 总数 | × -0.4(>0 比例) |
| stop_reason="end_turn" | `stop_reason` | +0.2(正常结束) |
| error_type 非空 | `error_type` | -0.5 |
| skill_activate success | `audit_signals.skill_activations` 全 status=ok | +0.1 |
| skill_activate error | 同上 status=error | -0.2 |

最终 `clip(sum, -1.0, +1.0)`。

### `CriticReward`

复用 B4 `CriticAgent`(已在 `auxiliary_clients.name='critic'` 装载)。每条
trajectory 喂 critic 一段 system + assistant + tool_use trace,要 critic 给
`{"score": 1-10, "reason": "..."}` JSON。本类把 1-10 线性映射到 [-1, +1]
(`(score - 5.5) / 4.5`),`reason` 落进 `breakdown.critic_reason`。

未装 critic → `from_agent` 返 None,annotator 退化纯静态(weights["critic"] 自动归 0)。

### Surface(wave 2)

- CLI:`chariot rl annotate <input.jsonl> --out <out.jsonl>`
  - `--weights "static=0.6,critic=0.4"` 可调
  - `--no-critic` 强制跳过 critic 调用(快路径,本地预览用)
- sidecar:`annotate_trajectory({in_path, out_path, weights?, no_critic?})` 返
  `{row_count, mean_reward, has_critic}`
- 进度展示:静态 reward 同步算,critic 异步 batch(并发上限 4,避免压垮 LLM)

### 测试(wave 2)

- `tests/platform/test_static_reward.py`:每条 sub-rule 单测 + 综合 case
- `tests/platform/test_critic_reward.py`:用 MockProvider 返固定 JSON,验证
  解析 + 1-10 → [-1, +1] 映射;critic 抛错时 fallback 0
- `tests/platform/test_reward_annotator.py`:weights 加权 / 缺 critic 退化 /
  jsonl roundtrip

---

## Wave 3 详细设计:Dataset packager + Golden eval

### `DatasetPackager`

把多条 annotated trajectory JSONL 合成单个 dataset。功能:

```python
class DatasetPackager:
    @dataclass(frozen=True)
    class DatasetSummary:
        total: int
        train: int
        eval: int
        mean_reward: float
        reward_buckets: dict[str, int]  # {"pos": N, "zero": N, "neg": N}
        out_path: Path

    async def pack(
        self,
        in_paths: list[Path],
        *,
        out_path: Path,
        train_ratio: float = 0.9,
        dedupe: bool = True,
        format: Literal["trl-sft", "trl-dpo", "raw"] = "trl-sft",
    ) -> DatasetSummary: ...
```

去重:按 `(prompt_hash, response_hash)` SHA256,默认开。

格式:
- `trl-sft`:`{"prompt": str, "completion": str, "reward": float}`(SFT 格式;
  prompt/completion 是 chat-template 之后的扁平字符串)
- `trl-dpo`:**wave 3 不做** —— DPO 要 chosen/rejected 配对,本期 trajectory
  没两两对照;留 B8
- `raw`:直接透传 ExportEntry,留给用户自己 ETL

split:按 `reward` 分层抽样,保证 train / eval 的 reward 分布近似。

### `GoldenEvalRunner`

```python
class GoldenEvalRunner:
    """跑预置 golden tasks → 产 trajectory → annotate → 进 dataset。"""

    @dataclass(frozen=True)
    class GoldenTask:
        id: str                # "smoke_read_dir" / "smoke_propose_skill" / ...
        goal: str              # user message text
        expected_tools: list[str] | None  # 期望调到的 tool(可选;不强校验)

    async def run(self, agent: AIAgent, *, eval_db: Path) -> list[ExportEntry]: ...
```

3 条内置 golden task(初版,后续按 demo 跑通后再补):
1. `smoke_read_dir`:goal "list files in . then read README.md" → 期望调
   `list_dir` + `read_file`
2. `smoke_skill_activate`:goal 含 `--skill code_review`,看 system 注入是否生效
3. `smoke_guardrail_blocked`:goal "delete /tmp/x" → 期望 shell_rm_rf 命中 DENY

跑 golden 走 **独立 sqlite**(`--eval-db <path>`,默认 `~/.chariot/rl/eval-{ulid}.db`),
不污染用户主库。

### Surface(wave 3)

- CLI:
  - `chariot rl golden run [--eval-db path]` → 产 `~/.chariot/rl/golden-runs/<ulid>.jsonl`
  - `chariot rl pack <input-dir-or-glob> [--out path] [--format trl-sft] [--train-ratio 0.9]`
  - `chariot rl summary <packed.jsonl>` —— 读 packed dataset,打印 total /
    mean_reward / reward 分布
- sidecar:`run_golden_eval` / `pack_dataset` / `summarize_dataset` 三个 RPC

### 测试(wave 3)

- `tests/platform/test_dataset_packager.py`:dedupe / split / 格式映射 / 边界
  (空 input)
- `tests/platform/test_golden_runner.py`:`MockProvider` 返预录响应,跑 3 条
  golden task → trajectory 行数 + tool_call 命中

---

## Wave 4 详细设计:桌面 /rl 页 + demo doc §7.18 + LONGTERMPLAN 占位

### 桌面 `/rl` 页

`packages/app/src/pages/RL.tsx`,三 tab:

- **Trajectories**:列最近导出的 trajectory 文件(扫 `~/.chariot/rl/trajectories/`
  目录;每行 conv_id / row_count / mean_reward / created_at),点击 → 预览前 5 行
- **Reward**:展示 dataset 的 reward 分布直方图(简单 ASCII 柱状用 div 高度模拟,
  不依赖 chart 库);"Re-annotate" 按钮触发后台 RPC
- **Dataset**:列 `~/.chariot/rl/datasets/` 下的 packed 文件,显示
  `DatasetSummary`(total / train / eval / mean_reward / buckets);
  "Pack new" 按钮弹 modal 输入 input dir / out path

API 加 5 个 method:`exportTrajectory` / `annotateTrajectory` / `runGoldenEval` /
`packDataset` / `summarizeDataset`。

### Demo doc §7.18

`docs/guides/agent-binding-demo.md` 加 §7.18 "RL 数据 pipeline(B7 wave 1-4)":
- 概念:trajectory / reward / dataset 三段
- CLI 5 个子命令快速参考
- 数据流图(conversation → trace → export → annotate → pack)
- 验证步骤(跑 export → annotate → pack → summary)

### LONGTERMPLAN.md 占位

§16 "当前结论" 后加 §17 "B8+ 真正的 RL 训练":列接外部 trainer 的入口
(trl `SFTTrainer` / verl / unsloth),格式对齐说明,**不**承诺时间线。

### 风险 / 决策

- **trajectory 大文件**:一条 conversation 上千 turn 可能产 MB 级 JSONL;
  CLI 不 chunk,sidecar 走 line-by-line stream(避免一次加载到内存)
- **critic 调用成本**:批量 annotate 走并发上限 + cache(`{prompt_hash:
  score}` 内存 LRU 单次进程),避免重复评分
- **secret scrubber 误伤**:正则集合保守(只匹配明显模式),false positive
  比 false negative 危害大;`--raw` 兜底
- **dataset 跟外部 trainer 格式**:不内嵌训练框架,只导出 `trl-sft` 兼容
  格式;后续兼容性看 trl 自己 release notes
- **golden eval 数量太少**:wave 3 起 3 条 smoke,够"跑通 pipeline";扩 N
  条留 B8(eval suite 是 LONGTERMPLAN §13 的范围)
- **B6 audit 信号利用率**:目前 `skill_activate.is_error` 字段还没有
  upstream 在写(`_maybe_activate_skill` 走 success 路径才记 ok)。B7 wave 2
  跑 reward 时若需"skill 失败"信号,要回 B6 加 stream_done error → 补一条
  skill_activate is_error=True(留作 B7 wave 2 实施时的依赖项)

---

## 跨 milestone 依赖

- 依赖 B4 critic agent 可用(`auxiliary_clients.name='critic'`)→ 缺 critic
  退化纯静态,功能不阻断
- 依赖 B5 audit_events(`guardrail_verdict` / `reflection`)→ 已落
- 依赖 B6 `skill_activate` audit → 已落
- 依赖 B1 trace 三表(`trace_turns` / `trace_provider_calls` / `trace_tool_calls`)
  → 已落

不依赖 LONGTERMPLAN.md §13 "Milestone B4: Evaluation and learning loop" 提到
的 `eval_runs` / `eval_cases` / `eval_results` 表 —— 那是另一条平行线
(eval-driven QA vs RL-driven training),B7 范围内不动 eval 表 schema。
golden eval 跑出的 trajectory 直接进 RL pipeline,不写 eval_runs。
