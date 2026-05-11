# B5 — Guardrails + Checkpoint + Audit

> Milestone:`feat/0.8.4-guardrails`
> 范围:补 phalanx §2.8.d 的 4 子能力 — Tool guardrails / Checkpoint 三件套 / Audit 自动 hook / Capability gating
> 总规划上下文:`docs/evolution-design.md` §6.5 + §8(安全前置红线)
> 设计前提:复用已有 v7 落地的 `audit_events` + `checkpoints` 表;现有 `AuditRepo` / `CheckpointRepo` / `chariot checkpoint list` 已有,本 milestone 把它们从"骨架"补成"完整能力"
> 模块归属:guardrails 是工具调用的拦截层,落 `chariot/guardrails/`(顶层独立模块,跟 `chariot/audit/` / `chariot/checkpoints/` 同级);3 个领域内聚

## 安全前置红线(evolution-design §8)

任何启用 "agent 改 chariot 自身" 的能力(B6 skill propose 等)之前,这 milestone
必须 ship:

1. **所有自我修改操作走 checkpoint** — write 前自动 snapshot,可一键 rollback
2. **Trajectory 永久 audit trail** — `audit_events` 表 + 五类自动 hook + CLI / sidecar 读
3. **Capability gating 默认关闭** — `--enable-self-mod` opt-in;`--yolo` 跳过所有审批

B5 就是为 B6 skill propose 这条线"先建好护栏"。

## Wave 拆分

| Wave | 内容 |
|---|---|
| 1 | Guardrails 核心:`chariot/guardrails/` 子包 + `BaseRule` ABC + 13 内置危险命令 regex 规则 + `GuardrailVerdict`(ALLOW/REQUIRE_APPROVAL/DENY)+ 日配额计数 + `GuardrailEngine`;接入 `tools/execution.py` pre-call;CLI `chariot guardrail list / try`;sidecar `list_guardrails / try_guardrail`;tests |
| 2 | Audit 自动 hook:`chariot/audit/hooks.py` 编排五类自动 hook(`tool_call_pre/post` / `guardrail_verdict` / `memory_store` / `checkpoint_create` / `rollback`);ToolExecutionService / MemoryRepo / CheckpointManager 调 hook;CLI `chariot audit list / show / tail`;sidecar `list_audit_events / get_audit_event`;tests |
| 3 | Checkpoint 实操 + Capability gating:`chariot/checkpoints/manager.py` 跑 git stash + SQLite `Connection.backup()` + `~/.chariot/{config.yaml,.env}` tarball;rollback 路径;`chariot/agent/config.py` 加 `capabilities` 配置(enable_self_mod / yolo)+ migration v21 持久化;CLI `chariot checkpoint {create, show, rollback, delete}` 完整 + `--yolo` 全局 flag;sidecar 相应 RPC;tests |
| 4 | 桌面 Security 页(三 tab:Guardrails / Audit / Checkpoints)+ demo doc §8 收尾 |

---

## Wave 1 详细设计:Guardrails 核心

### 模块布局

```
chariot/guardrails/
├── __init__.py
├── base.py          # BaseRule ABC + GuardrailVerdict + Verdict 枚举
├── engine.py        # GuardrailEngine:规则注册 + dispatch + 日配额
├── quota.py         # DailyQuota 类(per-rule 计数;reset 在 UTC 0 点)
└── builtin/         # 13 条内置规则,每条一个文件
    ├── __init__.py
    ├── shell_destructive.py   # rm -rf / chmod 777 / etc
    ├── git_force_push.py
    ├── db_drop.py
    └── ... (按 verb-category 一类一文件)
```

### BaseRule ABC + GuardrailVerdict

```python
class Verdict(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class GuardrailVerdict:
    rule_id: str                # 'shell_destructive' / 'git_force_push' / ...
    verdict: Verdict
    reason: str                  # "rm -rf 命中" / 自然语言
    matched_pattern: str | None  # 命中的具体 regex(给 audit 留证)
    quota_remaining: int | None  # 当前 rule 今日剩余配额(None = 不限)


class BaseRule(ABC):
    rule_id: ClassVar[str]
    description: ClassVar[str]
    verdict: ClassVar[Verdict]            # 命中后的默认 verdict
    daily_quota: ClassVar[int | None]     # None = 不限;数字 = 每天最多放过几次(超 → DENY)

    @abstractmethod
    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """命中返 matched_pattern;未命中返 None。"""
```

### 13 内置规则(初版)

| rule_id | tool target | 默认 verdict | daily_quota |
|---|---|---|---|
| `shell_rm_rf` | shell_exec | DENY | None |
| `shell_chmod_unsafe` | shell_exec | REQUIRE_APPROVAL | 5 |
| `shell_git_push_force` | shell_exec | REQUIRE_APPROVAL | 3 |
| `shell_curl_pipe_sh` | shell_exec | DENY | None |
| `shell_dd_block_device` | shell_exec | DENY | None |
| `shell_format_disk` | shell_exec | DENY | None |
| `db_drop_table` | shell_exec / write_file | DENY | None |
| `db_truncate_table` | shell_exec | REQUIRE_APPROVAL | 3 |
| `file_write_secrets` | write_file | REQUIRE_APPROVAL | 3 |
| `file_write_outside_cwd` | write_file | DENY | None |
| `http_post_unsafe` | http_get / http_post | REQUIRE_APPROVAL | 10 |
| `network_exfil` | shell_exec(scp / rsync)| REQUIRE_APPROVAL | 5 |
| `self_modify_chariot` | write_file targeting `chariot/` | DENY(默认)→ ALLOW(enable_self_mod=True) | 见 wave 3 |

最后一条 `self_modify_chariot` 是 B5 / B6 联动:默认 DENY,wave 3 加
`capabilities.enable_self_mod` 后变 REQUIRE_APPROVAL。

### GuardrailEngine

```python
class GuardrailEngine:
    def __init__(self, *, rules: list[BaseRule] | None = None) -> None: ...

    @classmethod
    def with_defaults(cls) -> Self:
        """装 13 个内置规则。"""

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> GuardrailVerdict:
        """跑所有规则:
        - 任一规则命中 DENY → 返 DENY(短路)
        - 任一规则命中 REQUIRE_APPROVAL → 返 REQUIRE_APPROVAL(若无 DENY)
        - 全部未命中 → 返 ALLOW(rule_id='_default')
        - 配额超限 → 自动降级:REQUIRE_APPROVAL → DENY
        """
```

### 接入 `tools/execution.py`

`ToolExecutionService.execute_tool_call` 在调用 `tool.execute(...)` 前:

```python
verdict = self._guardrail_engine.evaluate(tool_name, tool_input)
if verdict.verdict == Verdict.DENY:
    return ChatEvent.tool_result_event(
        tool_use_id=tool_use_id,
        content=f"guardrail denied: {verdict.rule_id}: {verdict.reason}",
        is_error=True,
    )
if verdict.verdict == Verdict.REQUIRE_APPROVAL:
    if not self._approval_policy.auto_approve(tool_name):
        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=f"guardrail requires approval: {verdict.rule_id}: {verdict.reason}",
            is_error=True,
        )
# ALLOW → 继续正常 execute
```

`_approval_policy.auto_approve` 在 wave 3 接 `--yolo` flag;wave 1 默认所有
REQUIRE_APPROVAL 都不放行(对应"互动审批未实现先一律拒绝"的保守语义)。

### Surface(wave 1)

- CLI `chariot guardrail list`:列规则 + 当日配额剩余
- CLI `chariot guardrail try <tool_name> --args '{"command": "rm -rf /"}'`:dry-run 一次评估
- sidecar `list_guardrails` / `try_guardrail` RPC
- 桌面 wave 4 才加

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §8.1。

---

## Wave 2 详细设计:Audit 自动 hook

### 五类自动 hook

| event_type | 触发点 | payload 关键字段 |
|---|---|---|
| `tool_call_pre` | `ToolExecutionService.execute_tool_call` 入口 | tool_name / args / turn_id |
| `tool_call_post` | `ToolExecutionService.execute_tool_call` 退出 | tool_name / status / duration_ms / is_error |
| `guardrail_verdict` | `GuardrailEngine.evaluate` 命中(verdict≠ALLOW) | rule_id / verdict / matched_pattern |
| `memory_store` | `MemoryRepo.create / update` 调用 | memory_id / kind / pinned |
| `checkpoint_create` | `CheckpointManager.create` 调用 | checkpoint_id / kind / target |
| `rollback` | `CheckpointManager.rollback` 调用 | checkpoint_id / restored_files |

`audit_events` 表 v7 已有,wave 2 不加 migration,只新增 hook 触发点。

### `AuditHookManager`

```python
class AuditHookManager:
    def __init__(self, sessionmaker: async_sessionmaker | None) -> None: ...

    async def record(
        self,
        event_type: str,
        *,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """best-effort 写 audit_events;sessionmaker=None 退化 no-op,异常吞掉。"""
```

无状态、可重入。AIAgent 在 bootstrap 时构造一个 `AuditHookManager` 实例,作为
field 暴露给 ToolExecutionService / MemoryRepo / CheckpointManager。

### 接入点

- `ToolExecutionService.__init__` 接收 `audit_hooks: AuditHookManager | None`;
  pre/post 各调一次 `audit_hooks.record(...)`
- `GuardrailEngine.evaluate` 调用方(就是 ToolExecutionService)在命中 verdict
  ≠ ALLOW 时调 `audit_hooks.record("guardrail_verdict", ...)`
- `MemoryRepo.create / update` 在 commit 后写 `memory_store` event(走 hook
  manager,不直接 import audit_repo;hooks_manager.record 通过 sessionmaker
  开自己的 session,避免外层事务问题)
- `CheckpointManager.create / rollback` wave 3 才接入

### Surface(wave 2)

- CLI `chariot audit list [--limit N] [--type X] [--status Y]`:筛筛筛
- CLI `chariot audit show <event_id>`:展开 payload
- CLI `chariot audit tail`:类似 `tail -f`,每秒轮询新事件(简化:无 SSE,纯轮询)
- sidecar `list_audit_events / get_audit_event` RPC(已有 AuditRepo,直接包装)
- 桌面 wave 4 才加

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §8.2。

---

## Wave 3 详细设计:Checkpoint 实操 + Capability gating

### v21 migration

```sql
CREATE TABLE capabilities (
    name TEXT PRIMARY KEY,    -- 'enable_self_mod' / 'yolo'
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO capabilities (name, enabled) VALUES ('enable_self_mod', 0), ('yolo', 0);
PRAGMA user_version = 21;
```

Per-process flag(`--yolo`)在 CLI 层短路 enable_self_mod 检查;DB 持久化版用
于 sidecar / 长期任务 / 多 surface 共享。

### CheckpointManager 三件套

```python
class CheckpointManager:
    def __init__(self, *, repo: CheckpointRepo, db_path: Path, audit_hooks: AuditHookManager | None = None): ...

    async def create(self, name: str) -> CheckpointEntry:
        """三件套:
        1. git stash push -u(unsaved 改动)→ 记 stash id
        2. SQLite Connection.backup() → 落到 ~/.chariot/checkpoints/<name>.sqlite
        3. tarball ~/.chariot/{config.yaml,.env} → ~/.chariot/checkpoints/<name>.tgz
        meta 三段写进 CheckpointRepo.create(name, kind='full', payload={...})。
        """

    async def rollback(self, checkpoint_id: str) -> RollbackResult:
        """三件套反向:
        1. git stash apply <stash_id>(若 stash 还在)
        2. 拷 SQLite backup 回 chariot.db(stop sm → 拷 → restart)
        3. 解 tarball 覆盖 ~/.chariot/{config.yaml,.env}
        每步可单独 fail,逐项报告;失败的项不阻其它项。
        """

    async def delete(self, checkpoint_id: str) -> None:
        """rm 落盘的 sqlite / tgz;DB 记录走 CheckpointRepo.delete"""
```

`RollbackResult` 数据对象:`git_ok / db_ok / config_ok / errors: list[str]`。

### Capability gating

`chariot/agent/config.py` 加 `Capabilities` dataclass:

```python
@dataclass(frozen=True)
class Capabilities:
    enable_self_mod: bool = False
    yolo: bool = False  # process-level only;不持久化(每次启动重置)

    @classmethod
    async def from_db(cls, session: AsyncSession, *, yolo_override: bool = False) -> Self: ...
```

`ApprovalPolicy`(wave 1 stub)在 wave 3 接 capabilities:

```python
class ApprovalPolicy:
    def auto_approve(self, tool_name: str) -> bool:
        if self._capabilities.yolo:
            return True
        # 后续可扩展:per-tool 白名单 / 互动审批等
        return False
```

### CLI / sidecar surface

- CLI:
  - `chariot checkpoint create <name>`(新)
  - `chariot checkpoint show <id>`(新)
  - `chariot checkpoint rollback <id> [--git-only|--db-only|--config-only]`(新)
  - `chariot checkpoint delete <id>`(新)
  - `chariot --yolo <subcommand>`(新全局 flag;走 ChariotConfig.capabilities patch)
  - `chariot capability {list, enable, disable}`(新;持久化 capabilities 表)
- sidecar:`create_checkpoint / show_checkpoint / rollback_checkpoint / delete_checkpoint / list_capabilities / set_capability`

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §8.3。

---

## Wave 4 详细设计:桌面 Security 页 + 收尾

### 桌面 Security 页

`packages/app/src/pages/Security.tsx`(新),Tabs 三标签:

- **Guardrails 标签**:规则列表 + 当日配额剩余;顶部"Test rule"小工具(输入
  tool_name + args JSON → 调 `try_guardrail`)
- **Audit 标签**:event timeline,按时间降序;筛选 by event_type / status;点
  事件展开 payload JSON
- **Checkpoints 标签**:checkpoint 列表 + "Create checkpoint" 按钮(prompt 输入
  name)+ 每条行 "Rollback" / "Delete" 按钮(带二次确认 dialog)

### Demo 文档收尾

- `docs/guides/agent-binding-demo.md` §8.1 / §8.2 / §8.3 校准到实际 CLI
- 加 §8.4 完整 self-mod 防护链路演示(默认 deny → enable_self_mod → require_approval → --yolo → allow + audit)

### 验收

- `chariot guardrail list` 列 13 条规则
- `chariot guardrail try shell_exec --args '{"command":"rm -rf /"}'` → DENY
- `chariot audit list --type tool_call_pre` 能看到 tool_call 事件流水
- `chariot checkpoint create test1` → `chariot checkpoint rollback <id>` → 改动回滚
- `chariot --yolo chat "..."` 时一个 REQUIRE_APPROVAL 工具能放过(并写 audit)
- 桌面 Security 页三 tab 都能跑通
- 全 pytest / ruff / pyright / bun build 通过

---

## 风险 / 决策

- **规则误判?** → 13 内置规则只是"显著危险动作"的样本集;DENY 直接拒绝,可
  通过 `chariot capability enable enable_self_mod` 放行 self_mod 类;
  REQUIRE_APPROVAL 类在没有互动审批 UI 时(B5 wave 1)一律保守拒。未来 B6 起接 UI
- **Rollback 不完全?** → git stash 不覆盖 git 不 track 的二进制 / 大文件;
  SQLite backup 期间 chariot 自己写 DB 不影响(`Connection.backup` 是 online);
  config tarball 是替换语义,丢了 backup 之外的新增项。3 路径各自报 ok/fail,
  不强一致
- **audit_events 增长?** → 写多读少;v8 留 retention policy(B5 不做,扔给 B7 RL)
- **跟 B4 reflection 关系**?→ 没冲突。Guardrail 是工具调用前置拦截,reflection
  是输出回路;guardrail DENY 直接进 tool_result is_error=True,reflection 触发
  条件 `tool_failure` 会命中(等于:危险动作被拦后,critic 来 advise next step)
- **--yolo 风险**?→ 写明只用于沙箱机器 / CI;guardrail 仍记 audit_event,只是
  不阻断
