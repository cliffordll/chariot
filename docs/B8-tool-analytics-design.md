# B8 — 工具管理（Tool Management）

> Milestone:`feat/0.8.7-toolmngn`
> 范围:基于 B5 audit + B6 curator 模式,为工具层补全"可观测、可评估、可优化"闭环
> 设计前提:
> - B5 `audit_events` 已自动记录 `tool_call_pre/post`(含 status / error / duration)
> - B6 `SkillCurator` 4-bucket 模式已验证有效
> - `ToolRepo` 已有基础 CRUD,缺统计查询
> - 模块归属:`chariot/tools/` 子包内新增 `analytics.py` / `curator.py`,不另开顶层目录

---

## 0. 为什么现在做

B3-B7 补完了 agent 的"记忆-反思-安全-技能-训练"闭环,但**工具层本身的管理能力仍然薄弱**:

| 能力 | Skills(B6) | Tools(当前) | 差距 |
|---|---|---|---|
| 注册 | ✅ YAML + DB 双源 | ✅ 代码注册 + DB 配置 | 平 |
| 启用/禁用 | ✅ `set_enabled` | ✅ `enable/disable` | 平 |
| 使用统计 | ✅ `skill_activate` audit | ◇ 有 `tool_call_pre/post` audit,**无聚合视图** | **缺** |
| 健康检查 | ✅ `SkillCurator` 4-bucket | ✗ **无** | **缺** |
| 报告生成 | ✅ `curate` CLI + RPC | ✗ **无** | **缺** |

工具是 agent 与外部世界交互的唯一手段。不知道"哪些工具常用、哪些总失败、哪些该退役" = 盲目扩展工具集,agent 效率不升反降。

**本 milestone 不做**:
- 动态工具创建(codegen)—— 高风险,留远期
- 工具权重自动调参(Bandit)—— 依赖 B7 RL 数据积累
- 自定义工具 YAML 注册 —— 0.8.x 以内置工具为主,外部工具走 `toolset` 已有

---

## 1. Wave 拆分

| Wave | 内容 | 交付 | 阻塞 |
|---|---|---|---|
| **1** | Tool Analytics 基建 — `ToolAnalytics` 类读 audit_events 聚合统计;`ToolRepo` 补统计查询;CLI `chariot tool analytics`;tests | 工具使用数据可查询 | wave 2 |
| **2** | Tool Curator — `ToolCurator` 4-bucket 静态分类(failing / underused / stale / overused);CLI `chariot tool curate`;sidecar RPC;tests | 工具健康度可评估 | wave 3 |
| **3** | Tool Report + 桌面 UI — `ToolReporter` 生成 markdown/HTML 报告;桌面 `/tools` 页加 Analytics tab;demo doc 收尾 | 工具报告可呈现 | — |

> 3 wave 总量对标 B6 wave 4(但无 propose 的复杂安全链路),预计 2-3 周。

---

## 2. Wave 1: Tool Analytics 基建

### 2.1 问题

`audit_events` 已有这些事件:
- `tool_call_pre` —— 调用前(payload: tool_name, arguments)
- `tool_call_post` —— 调用后(payload: tool_name, is_error, duration_ms, result_snippet)

但**没有聚合查询**:
- "过去 30 天 read_file 调了多少次、失败率多少、平均耗时多少"
- "哪些工具一次都没被用过"
- "哪个工具失败率最高"

### 2.2 数据形态

```python
@dataclass(frozen=True)
class ToolUsageStat:
    """单工具在查询窗口内的聚合统计。"""

    tool_name: str
    total_calls: int           # 总调用次数
    success_calls: int         # 成功次数(is_error=False)
    error_calls: int           # 失败次数
    success_rate: float        # success_calls / total_calls
    avg_duration_ms: float | None   # 平均耗时(毫秒);无调用则 None
    p95_duration_ms: float | None   # P95 耗时
    last_called_at: datetime | None # 最后一次调用时间


@dataclass(frozen=True)
class ToolAnalyticsResult:
    """一次 analytics 查询的完整结果。"""

    window_days: int                   # 查询窗口(默认 30)
    generated_at: datetime             # 生成时间
    stats: list[ToolUsageStat]         # 每个工具的统计(按 tool_name 字母序)
    top_by_volume: list[str]           # 调用量 top-N 工具名
    top_by_error_rate: list[str]       # 失败率 top-N 工具名(只含 total_calls >= 5 的)
    unused_tools: list[str]            # 窗口内零调用的工具名
```

### 2.3 `ToolAnalytics` 类

```python
class ToolAnalytics:
    """工具使用分析器。

    只读;**不写 DB**。从 audit_events + tools 表聚合,返结构化结果。
    用法::

        analytics = ToolAnalytics(sessionmaker=sm)
        result = await analytics.analyze(window_days=30)
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None: ...

    async def analyze(
        self,
        *,
        window_days: int = 30,
        min_calls_for_error_rank: int = 5,
    ) -> ToolAnalyticsResult:
        """跑聚合查询。

        逻辑:
        1. 读 `tools` 表拿全部工具名(包括 disabled 的,用于识别 unused)
        2. 读 `audit_events` 中 `tool_call_pre` / `tool_call_post` 对,
           按 `tool_name` + `created_at` 在窗口内聚合
        3. 配对 pre/post 算 duration(优先用 post payload, fallback post-pre 时间差)
        4. 生成 `ToolUsageStat` 列表 + 派生 top/unused 列表
        """
```

### 2.4 `AuditRepo` 扩展

在 `AuditRepo` 中加两个查询方法(不复用现有 `list_events`,避免一次性拉全表):

```python
# chariot/repos/audit_repo.py

async def list_tool_calls(
    self,
    *,
    tool_name: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    limit: int = 10000,
) -> list[AuditEvent]: ...

async def aggregate_tool_stats(
    self,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[ToolStatRow]: ...
```

`aggregate_tool_stats` 走 SQL 聚合(比 Python 侧聚合快 10x+):

```sql
SELECT
    payload->>'tool_name' as tool_name,
    COUNT(*) as total_calls,
    SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_calls,
    AVG(CAST(payload->>'duration_ms' AS INTEGER)) as avg_duration_ms,
    MAX(created_at) as last_called_at
FROM audit_events
WHERE event_type = 'tool_call_post'
  AND created_at BETWEEN :start AND :end
GROUP BY payload->>'tool_name'
```

### 2.5 `ToolRepo` 扩展

补一个轻量查询:

```python
async def list_all_names(self) -> list[str]:
    """返回全部工具名(含 disabled),字母序。"""
```

### 2.6 Surface

**CLI**:
```bash
# 默认 30 天窗口
chariot tool analytics

# 指定窗口
chariot tool analytics --days 7

# 输出 JSON(给脚本/CI 消费)
chariot tool analytics --json
```

输出示例:
```
┌─────────────┬───────┬─────────┬──────────┬──────────────┬─────────────────┐
│ tool_name   │ calls │ success │ error_rate │ avg_ms     │ last_called     │
├─────────────┼───────┼─────────┼──────────┼──────────────┼─────────────────┤
│ read_file   │ 142   │ 138     │ 2.8%     │ 45 ms        │ 2026-05-12 09:30│
│ list_dir    │ 89    │ 89      │ 0.0%     │ 12 ms        │ 2026-05-12 09:31│
│ shell_exec  │ 12    │ 8       │ 33.3%    │ 1200 ms      │ 2026-05-11 14:20│
│ write_file  │ 0     │ 0       │ —        │ —            │ —               │
└─────────────┴───────┴─────────┴──────────┴──────────────┴─────────────────┘

top by volume: read_file, list_dir, shell_exec
top by error rate: shell_exec (33.3%), read_file (2.8%)
unused: write_file, http_get, http_post
```

**sidecar RPC**(wave 2 一起加):
```json
{
  "method": "get_tool_analytics",
  "params": { "window_days": 30 }
}
```

### 2.7 测试

- `test_tool_analytics_empty` —— 无 audit 数据时全 zero/None
- `test_tool_analytics_basic` —— 造几条 audit events,验证聚合正确
- `test_tool_analytics_unused` —— disabled 工具也出现在 unused 列表
- `test_tool_analytics_window` —— 改 window_days 只统计窗口内

---

## 3. Wave 2: Tool Curator

### 3.1 问题

Analytics 给了原始数据,但人每天看表格不现实。需要**自动分类** + **可操作建议**:

- "write_file 30 天没被调用过 → 建议禁用,减少 agent 幻觉调用"
- "shell_exec 失败率 33% → 建议检查配置或加 guardrail"
- "read_file 每天调用 50 次 → 建议检查是否需要 context compression(B3)"

### 3.2 4-bucket 分类

对标 B6 `SkillCurator`,但 bucket 定义适配工具特性:

| bucket | 定义 | 阈值(经验值) | 建议动作 |
|---|---|---|---|
| `failing` | 失败率 > 20% 且总调用 ≥ 5 | `failing_ratio=0.2`, `min_calls=5` | 检查配置 / 加 guardrail / 降级 |
| `underused` | 总调用 < 3 | `underused_threshold=3` | 考虑禁用或宣传(agent 不知道它的存在) |
| `stale` | 最近 30 天无调用 | `stale_days=30` | 考虑禁用 |
| `overused` | 日均调用 > 20 次 | `overused_daily=20` | 检查是否需要 context compression / 技能固化(B6) |

> 互不独占:同一工具可被多个 bucket 命中(如 shell_exec 可能同时 failing + overused)。

### 3.3 `ToolCurator` 类

```python
@dataclass(frozen=True)
class ToolCurateResult:
    failing: list[str] = field(default_factory=list)
    underused: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    overused: list[str] = field(default_factory=list)


class ToolCurator:
    """工具静态分类器。

    只读;**不**自动改 tools.enabled —— 所有建议交人决定。
    用法::

        curator = ToolCurator(sessionmaker=sm)
        result = await curator.curate()
    """

    DEFAULT_STALE_DAYS = 30
    DEFAULT_UNDERUSED_THRESHOLD = 3
    DEFAULT_FAILING_RATIO = 0.2
    DEFAULT_MIN_CALLS_FOR_FAILING = 5
    DEFAULT_OVERUSED_DAILY = 20

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        stale_days: int = DEFAULT_STALE_DAYS,
        underused_threshold: int = DEFAULT_UNDERUSED_THRESHOLD,
        failing_ratio: float = DEFAULT_FAILING_RATIO,
        min_calls_for_failing: int = DEFAULT_MIN_CALLS_FOR_FAILING,
        overused_daily: int = DEFAULT_OVERUSED_DAILY,
    ) -> None: ...

    async def curate(self) -> ToolCurateResult:
        """跑 analytics → 4-bucket 分类。"""
```

### 3.4 Surface

**CLI**:
```bash
chariot tool curate
```

输出示例:
```
failing (2):
  shell_exec — 33.3% 失败率 (8/24), 建议:检查配置或加 guardrail
  http_post  — 25.0% 失败率 (2/8),  建议:检查 allowed_domains

underused (2):
  write_file — 1 次调用, 建议:确认 agent 是否需要写文件能力
  http_get   — 0 次调用, 建议:禁用或宣传给 agent

stale (1):
  http_post  — 最近调用 2026-04-01, 建议:禁用

overused (1):
  read_file  — 日均 47 次, 建议:检查 context compression 是否生效
```

**sidecar RPC**:
```json
{
  "method": "curate_tools",
  "params": {}
}
```

### 3.5 测试

- `test_curate_failing` —— 造高失败率工具,命中 failing bucket
- `test_curate_underused` —— 造低频工具,命中 underused
- `test_curate_stale` —— 造旧调用记录,命中 stale
- `test_curate_overused` —— 造高频调用,命中 overused
- `test_curate_multi_bucket` —— 同一工具命中多个 bucket

---

## 4. Wave 3: Tool Report + 桌面 UI

### 4.1 `ToolReporter`

```python
class ToolReporter:
    """生成工具使用报告。"""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None: ...

    async def generate(
        self,
        *,
        window_days: int = 30,
        format: Literal["markdown", "json"] = "markdown",
    ) -> str: ...
```

**Markdown 报告结构**:
```markdown
# Tool Usage Report (2026-05-01 ~ 2026-05-31)

## Summary
- 7 tools registered, 5 enabled
- 1,234 total calls, 98.2% success rate
- 1 tool failing, 2 tools unused

## Detailed Stats
| Tool | Calls | Success | Avg ms | Last Used |
|------|-------|---------|--------|-----------|
| ...  | ...   | ...     | ...    | ...       |

## Recommendations
1. **Disable `write_file`**: unused for 30 days
2. **Check `shell_exec` config**: 33% failure rate
3. **Review `read_file` frequency**: 47 calls/day, consider context compression
```

**CLI**:
```bash
chariot tool report [--days 30] [--out path.md]
```

### 4.2 桌面 UI

在现有 `/tools` 页(若 0.8.5+ 桌面已有 Tools 页)加 **Analytics** tab:

- 表格:工具名称 / 调用次数 / 成功率 / 平均耗时 / 最后调用
- 图表:调用量趋势(7/30/90 天)
- 告警 badge:failing / stale / overused 工具高亮
- Curator 按钮:一键跑分类,弹出建议列表

若桌面尚无 Tools 页,本 wave 只产 CLI + sidecar,桌面留 0.9.0+。

---

## 5. 模块布局

```
chariot/tools/
├── __init__.py          # re-export BaseTool / ToolRegistry / ToolEntry(已有)
├── base.py              # BaseTool ABC(已有)
├── registry.py          # ToolRegistry(已有)
├── builtin/             # 内置工具(已有)
├── analytics.py         # NEW: ToolAnalytics + ToolUsageStat + ToolAnalyticsResult
├── curator.py           # NEW: ToolCurator + ToolCurateResult
└── report.py            # NEW: ToolReporter

chariot/repos/
├── audit_repo.py        # EXTEND: + list_tool_calls / aggregate_tool_stats
└── tool_repo.py         # EXTEND: + list_all_names

chariot/cli/commands/
└── tool.py              # EXTEND: + analytics / curate / report 子命令

chariot/sidecar/methods/
└── __init__.py          # EXTEND: + get_tool_analytics / curate_tools RPC

tests/platform/
├── test_tool_analytics.py   # NEW
├── test_tool_curator.py     # NEW
└── test_tool_report.py      # NEW
```

---

## 6. 设计约束

### 6.1 只读

`ToolAnalytics` / `ToolCurator` / `ToolReporter` 全部只读,**不写 tools 表、不改 enabled 状态**。所有"建议禁用"只输出到报告,由人确认后走 `chariot tool disable`。

### 6.2 复用已有抽象

- 不复写 audit 查询逻辑 —— 扩 `AuditRepo`
- 不复写工具列表 —— 扩 `ToolRepo`
- curator 模式复用 B6 `SkillCurator` 结构(4-bucket + threshold + 只读)

### 6.3 性能

`aggregate_tool_stats` 走 SQL 聚合,不灌全表到内存。窗口默认 30 天,audit_events 按 `created_at` 有索引,查询毫秒级。

### 6.4 与 B7 RL 的关系

Tool analytics 数据是 B7 RL 的**输入之一**:
- `tool_call_post` 的 `is_error` + `duration_ms` 是 reward signal 组成
- `underused` / `stale` 工具 = agent 不知道用它们 → RL 需要教会 agent
- 本 milestone **不**直接产 RL 数据集,只提供聚合视图

---

## 7. 验收标准

### 7.1 wave 1
- [ ] `chariot tool analytics` 输出表格正确
- [ ] `--json` 输出可解析的 JSON
- [ ] `AuditRepo.aggregate_tool_stats` 单元测试通过
- [ ] 零调用工具出现在 unused 列表

### 7.2 wave 2
- [ ] `chariot tool curate` 4-bucket 分类正确
- [ ] 阈值可构造时覆写(测试用)
- [ ] sidecar `curate_tools` RPC 返回 JSON

### 7.3 wave 3
- [ ] `chariot tool report` 生成 markdown 文件
- [ ] 报告含 stats + recommendations 两段
- [ ] ruff / pyright / pytest 全绿

---

## 8. 风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| audit_events 数据量过大(>100万条)导致聚合慢 | 低 | SQL 聚合 + `created_at` 索引;真超了加 `LIMIT` |
| tool_name 在 payload 中不规范(如 null / 非字符串) | 中 | SQL 中 `payload->>'tool_name' IS NOT NULL` 过滤 |
| 用户没开 audit(`audit_events` 表空) | 中 | 空表时输出 "无数据,请确认 audit 已启用" banner |
| 与 B6 curator 代码重复度高 | 低 | 抽公共基类 `BaseCurator`(若 3 处以上重复再抽) |

---

## 9. 与 ROADMAP 的对齐

本 milestone 定位:
- **短期**:给管理员一把"工具健康度"尺子,替代手动翻 audit 表
- **中期**:为 B7 RL 提供工具层面的 reward feature(调用成功率、频率)
- **长期**:数据积累够了,可演进为"自动推荐 toolset 组合"(根据 task 类型动态选工具)

**不做**:动态工具创建、工具权重自动调参、工具市场 —— 这些需要更多数据和安全审查,留 0.9.x。
