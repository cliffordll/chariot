# B6 — Skills 生命周期

> Milestone:`feat/0.8.5-skills`
> 范围:phalanx §2.8.e 对齐 —— loader / activator / propose / curator;
> agent 从"只跑 tool"升级到"知道自己擅长哪类活、可以提议固化新能力"
> 设计前提:
> - v7 `skills` 表已落(`id / name / description / content / enabled / meta`)
> - `SkillRepo` 当前只有 `list/get/create`,wave 1 需补 `update / delete / set_enabled`
> - `BaseSkill` ABC 在 `evolution-design.md` 里提过但**实际未建**,wave 1 新增
> - 复用 B5 全链路:`GuardrailEngine` / `ApprovalPolicy` / `CheckpointManager` /
>   `AuditHookManager` / `Capabilities`(`enable_self_mod`)
> 模块归属:skills 是 agent 的"能力固化"机制,落 `chariot/skills/`(顶层独立子包,
>   跟 `chariot/audit/` / `chariot/checkpoints/` / `chariot/guardrails/` 同级)

## 安全前置红线(继承 B5 §8)

agent 自发 propose 新 skill = "agent 写 chariot 自己的能力定义",落进 `skills` 表
本质是自我修改的弱化版。所有 propose / install 路径**强制**串完整链路:

1. **guardrail** —— propose_skill 工具命中 `self_modify_chariot` rule(扩展匹配
   规则,见 wave 3),默认 DENY;`enable_self_mod=True` 时升级为
   REQUIRE_APPROVAL
2. **ApprovalPolicy** —— REQUIRE_APPROVAL 状态下,`yolo=True` 才放行,否则当
   DENY 拒
3. **auto-checkpoint** —— 命中允许后,**先**跑 `CheckpointManager.create(
   name=f"before-skill-propose-{skill_name}")`,失败 abort 整个 propose
4. **audit** —— 全程写 `audit_events`:`guardrail_verdict` / `checkpoint_create` /
   `skill_create`(新事件类型);propose 失败也写 `skill_create` with status="denied"
5. **配额** —— daily_quota=3(共用 guardrail `DailyQuotaTracker`),超额降级 DENY

任一环失败 → 不写 row、不留垃圾文件、直接给 agent 一条 tool_result is_error=True。

这条 milestone 不接 RL,但 reward signal 留接口:`audit_events` 里
`skill_activate.ok=true` + 接 B7 后跑过 golden task `verdict=PASS` 的 trajectory
天然是 positive sample。

## Wave 拆分

| Wave | 内容 |
|---|---|
| 1 | Skill loader — `BaseSkill` ABC + YAML manifest schema + `SkillRegistry`(扫 `chariot/skills/builtin/*.yaml` + DB `skills` 表 union)+ 3 内置 sample(`code_review` / `debug_helper` / `git_committer`)+ `SkillRepo` 扩 `update / delete / set_enabled` + CLI `chariot skill {list, show, install, enable, disable, remove}` + sidecar 6 个 RPC + tests |
| 2 | Skill activator — `SkillActivator` 类:把 `<skill name=...>...</skill>` 块拼进 `system`(在 `_prepare_request` 阶段)+ 工具过滤(只放 manifest 里 `allowed_tools` 集合)+ `ChatRequest.skill` 字段 + `agent_profile.default_skill` 字段(v22 migration)+ CLI `chariot chat --skill X` + REPL `/skill {list, <name>, clear}` + tests |
| 3 | Skill propose — `tools/builtin/propose_skill.py`:agent 自发提议新 skill,走完整 B5 链路;失败保留 audit + 不留 row。审批一旦放行:`CheckpointManager.create` → `SkillRepo.create` → `audit_events.skill_create` 三件套原子;CLI `chariot skill proposals`(read-only 看历史 propose) + tests |
| 4 | Curator + 桌面 Skills 页 + demo doc 收尾 — `chariot/skills/curator.py` 4-bucket 静态分类(stale / underused / failing / overlapping),read-only;`chariot skill curate` CLI + sidecar `curate_skills` RPC + 桌面 `/skills` 页(三 tab:Library / Proposals / Curator)+ demo doc §7.17 收尾 |

---

## Wave 1 详细设计:Skill loader

### 模块布局

```
chariot/skills/
├── __init__.py                # re-export BaseSkill / SkillRegistry / 已有 SkillEntry / SkillRepo
├── base.py                    # BaseSkill ABC + SkillManifest frozen dataclass
├── registry.py                # SkillRegistry:builtin/* YAML + DB skills union dispatch
├── loader.py                  # YAML 解析 + schema 校验(jsonschema 已是依赖)
└── builtin/
    ├── code_review.yaml       # 3 内置 sample
    ├── debug_helper.yaml
    └── git_committer.yaml
```

### YAML manifest schema

```yaml
# chariot/skills/builtin/code_review.yaml
schema_version: 1
name: code_review
version: "1.0.0"
description: 走 review pipeline:先 read 关键文件,再列改动 risk,再给 verdict
author: chariot-team
prompt: |
  你现在在做 code review。流程:
  1. 用 read_file 读改动文件
  2. 用 list_dir 看相关上下文
  3. 给出 verdict + risk 等级 + 修改建议
allowed_tools:
  - read_file
  - list_dir
forbidden_tools:
  - shell_exec
  - write_file
tags: [review, audit]
```

字段:
- `schema_version`:int,必填,目前固定 `1`(后续 schema 演进的版本号)
- `name`:str,必填,跨 builtin + DB 全局唯一,Python identifier 风格
- `version`:semver 字符串,默认 `"0.1.0"`,builtin 跟仓库一起 bump
- `description`:str,一句话给 list / show 用
- `prompt`:str,activator 注入 system prompt 的 `<skill>` 块内容
- `allowed_tools`:`list[str]` 或 `null`(null = 不限制)
- `forbidden_tools`:`list[str]`(永远不允许,即使 allowed_tools 列了)
- `tags`:`list[str]`,curator 用

校验:`jsonschema` 严格;非法 manifest → 加载时 `SkillManifestError`,**不** silent skip。

### BaseSkill ABC

```python
class BaseSkill(ABC):
    """单个 skill 的 Python 形态。

    子类来源两类:
    - `BuiltinSkill`:`builtin/*.yaml` 通过 `SkillLoader` 解析后构造
    - `DbSkill`:`skills` 表行(propose / install 路径产物)

    两者共用 ABC,registry 不区分;只在 `source` property 区分。
    """

    @property
    @abstractmethod
    def manifest(self) -> SkillManifest: ...

    @property
    @abstractmethod
    def source(self) -> Literal["builtin", "db"]: ...

    @property
    def name(self) -> str:
        return self.manifest.name

    def prompt_block(self) -> str:
        """activator 用:把 manifest.prompt 包成 `<skill name="...">...</skill>`。"""
        return f'<skill name="{self.name}">\n{self.manifest.prompt}\n</skill>'

    def tool_filter(self) -> "ToolFilter":
        """activator 用:返回 allowed / forbidden 二元组,dispatch 层应用。"""
        return ToolFilter(allowed=self.manifest.allowed_tools, forbidden=self.manifest.forbidden_tools)
```

`SkillManifest` 是 `@dataclass(frozen=True)`,跟 YAML schema 字段 1:1。
`ToolFilter` 是 frozen dataclass,wave 2 dispatch 用。

### SkillRegistry

```python
class SkillRegistry:
    """builtin/* YAML + DB skills 表 union 视图,name → BaseSkill。

    优先级:DB skill 同名覆盖 builtin(让用户能 patch 内置 skill,但 builtin 不删,
    只是被遮蔽);删 DB 行 → 重新暴露 builtin。
    """

    def __init__(self, *, builtin: dict[str, BaseSkill], db: dict[str, BaseSkill]) -> None: ...

    @classmethod
    async def load(cls, sessionmaker: async_sessionmaker[AsyncSession]) -> Self:
        """启动时调:扫 builtin/*.yaml + 读 skills 表 → 合并。"""

    def get(self, name: str) -> BaseSkill | None: ...
    def list_all(self) -> list[BaseSkill]: ...
    def list_enabled(self) -> list[BaseSkill]: ...  # DB 行 enabled=0 → 排除;builtin 永远 enabled
```

`load` 在 `AIAgent.bootstrap` 阶段调一次,挂 `self._skill_registry`;之后 propose /
install / enable / disable 后**不**热重载(改一次需要重启 sidecar / CLI 进程,
跟 provider/tool 同语义)。

### SkillRepo 扩展(wave 1 新增)

当前只有 `list_entries / get_entry / create`。补:
- `update(entry_id, *, description=None, content=None, meta=None)` → 编辑 manifest
- `delete(entry_id) -> bool`
- `set_enabled(entry_id, enabled: bool)`

写入路径加 audit:`audit_events` 加新事件类型 `skill_create` /
`skill_update` / `skill_delete`(由 wave 3 propose 路径触发,但 wave 1 给手工
install/enable 也加 hook;统一走 `AuditHookManager`)。

### Surface(wave 1)

- CLI:
  - `chariot skill list`(已存,enrich:加 source / tags / version 列)
  - `chariot skill show <name>`(新)
  - `chariot skill install --from <path-or-name>`(新;手动 install builtin → DB 让其可改 / 或从外部 YAML 文件加载)
  - `chariot skill enable <name>` / `disable <name>`(新)
  - `chariot skill remove <name>`(新)
- sidecar:`list_skills` / `get_skill` / `install_skill` / `enable_skill` /
  `disable_skill` / `delete_skill`
- 桌面 wave 4 才加

### 测试(wave 1)

- `tests/platform/test_skill_loader.py`:YAML schema 校验 / 非法字段 / 重复 name
- `tests/platform/test_skill_registry.py`:builtin + DB union 优先级 / load 幂等
- `tests/platform/test_skill_repo.py`:扩展的 update/delete/enable
- `tests/sidecar/test_skill_methods.py`:6 个 RPC dispatch

---

## Wave 2 详细设计:Skill activator

### `SkillActivator`

```python
class SkillActivator:
    """把 active skill 的 prompt / tool_filter 注入 ChatRequest。

    用法:
        skill = self._skill_registry.get(req.skill)
        if skill is not None:
            req = activator.activate(req, skill)
        # req.system 已包 <skill>... </skill> 块;req.tools 已按 filter 过滤

    封装:
    - 单类,无副作用;每次 activate 返新 req(`dataclasses.replace`)
    - 不持状态;registry 只是依赖
    """

    @staticmethod
    def activate(req: ChatRequest, skill: BaseSkill) -> ChatRequest: ...

    @staticmethod
    def apply_tool_filter(tools: list[ToolSchema], filter: ToolFilter) -> list[ToolSchema]: ...
```

### 接入点

1. `ChatRequest` 加 `skill: str | None = None`(对齐 `agent_profile`)
2. `AgentProfile` 加 `default_skill: str | None`(v22 migration)
3. `AIAgent._prepare_request` 在已有 prompt bundle / agent_profile 装配后,**追加**
   一段 skill activation:
   ```python
   skill_name = req.skill or (binding.profile.default_skill if binding.profile else None)
   if skill_name and self._skill_registry:
       skill = self._skill_registry.get(skill_name)
       if skill is not None and skill.manifest.enabled:
           req = SkillActivator.activate(req, skill)
   ```
4. dangling reference(`req.skill="ghost"`):skip activate,**不**报错(跟
   `agent_profile.prompt_bundle` 同语义,降级到无 skill)
5. 已经 inject 的 system bundle + skill prompt 顺序:**先 bundle 后 skill**,让
   skill 是"最后一层提示"

### CLI

- `chariot chat --skill <name>`(全新 flag)
- `chariot chat --skill ""` = 显式清空(覆盖 agent_profile.default_skill)
- REPL(`chariot chat` 进交互模式)新增 slash 命令:
  - `/skill list` —— 列可用 skill
  - `/skill <name>` —— 设当前 skill
  - `/skill clear` —— 清当前 skill
  REPL 状态挂在 chat session 上,持续到 `clear` 或会话结束

### v22 migration

```sql
ALTER TABLE agent_profiles ADD COLUMN default_skill TEXT;
PRAGMA user_version = 22;
```

### 测试(wave 2)

- `tests/platform/test_skill_activator.py`:activate 注入 / dangling fallback /
  tool filter
- `tests/platform/test_skill_profile.py`:agent_profile.default_skill 透传

---

## Wave 3 详细设计:Skill propose

### `propose_skill` tool

`chariot/tools/builtin/propose_skill.py`(新)。注册为内置 tool,默认 disabled
(`enable_self_mod=True` + 显式 `chariot tool enable propose_skill` 才上线),
匹配 guardrail `self_modify_chariot` 规则的扩展。

`input_schema`:
```json
{
  "type": "object",
  "required": ["name", "description", "prompt"],
  "properties": {
    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
    "description": {"type": "string", "minLength": 8, "maxLength": 200},
    "prompt": {"type": "string", "minLength": 16, "maxLength": 4000},
    "allowed_tools": {"type": "array", "items": {"type": "string"}},
    "forbidden_tools": {"type": "array", "items": {"type": "string"}},
    "tags": {"type": "array", "items": {"type": "string"}}
  }
}
```

### 全链路(原子)

```
propose_skill.execute(input):
  ├─ 1. ToolExecutionService 入口
  │    ├─ guardrail.evaluate → 命中 self_modify_chariot
  │    │     ├─ enable_self_mod=False → DENY → audit + return is_error
  │    │     └─ enable_self_mod=True → REQUIRE_APPROVAL
  │    └─ ApprovalPolicy.auto_approve → False → return is_error;True → 继续
  │
  ├─ 2. SkillProposeService.run(input):                ← 新类,负责剩余链路
  │    ├─ 2a. SkillRepo.get_by_name(input.name) → 存在?返"already exists" 错
  │    ├─ 2b. CheckpointManager.create(f"before-skill-propose-{input.name}")
  │    │     失败 → audit "skill_create" status="failed" + return is_error
  │    ├─ 2c. SkillRepo.create(name, description, content=YAML(input), enabled=False)
  │    │     失败 → audit + return is_error(checkpoint 留着,用户手工 rollback)
  │    ├─ 2d. audit_hooks.record("skill_create", status="ok", payload={
  │    │       skill_id, name, checkpoint_id, source: "propose", proposer: "agent"
  │    │     })
  │    └─ return tool_result content=f"proposed: {skill_id}, run `chariot skill enable {name}` to activate"
```

**约束**:
- propose 默认 `enabled=False` —— agent 提议完,**人**手动 `chariot skill enable` 才生效
- checkpoint 命名固定 `before-skill-propose-<name>`,方便 rollback 时找
- 任何一段失败:**保留 checkpoint**(用户可手工 rollback);**不留** skills 行
- 同 name 已存在 → 当成失败,不覆盖(走 `skill update` 路径,跟 propose 区分)

### `SkillProposeService`

```python
class SkillProposeService:
    """全链路 orchestrator,跟 ToolExecutionService 一样持 audit + checkpoint。

    封装策略(CLAUDE.md ⭐):
    - 单类编排;模块级零自由函数
    - 接 `sessionmaker` + `audit_hooks` + `checkpoint_manager`
    - 任何一段失败立即 return ProposeResult(ok=False, error=...);不抛
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        audit_hooks: AuditHookManager,
        checkpoint_manager: CheckpointManager | None,
    ): ...

    async def propose(self, input: ProposeInput) -> ProposeResult: ...
```

`AIAgent.bootstrap` 时构造一个挂在 `self._skill_propose`;propose_skill tool
拿 agent 引用调 `self_propose.propose(...)`(类似 ToolExecutionService 的注入)。

### Surface(wave 3)

- CLI:`chariot skill proposals`(列历史 propose audit events,read-only)
- sidecar:复用 `list_audit_events` filter by event_type=skill_create
- 不加新 RPC —— propose 是 agent 自发动作,不该走 surface 触发

### 测试(wave 3)

- `tests/platform/test_skill_propose_service.py`:
  - capability 关 → DENY
  - capability 开 + yolo 关 → REQUIRE_APPROVAL → DENY
  - capability 开 + yolo 开 → checkpoint + row + audit 三件套全写
  - 重名 → "already exists"
  - checkpoint 失败 → 不写 row,audit status="failed"
- `tests/platform/test_propose_skill_tool.py`:tool 装配 + ToolExecutionService 集成

---

## Wave 4 详细设计:Curator + 桌面 Skills 页 + 收尾

### `SkillCurator`

```python
class SkillCurator:
    """read-only 静态分类。所有 bucket 都是建议,不自动改 skills.enabled。"""

    @dataclass(frozen=True)
    class Result:
        stale: list[str]            # 30 天没被 activate
        underused: list[str]        # 总 activate 次数 < 3
        failing: list[str]          # 最近 10 次 activate 后 tool_result.is_error 比例 > 50%
        overlapping: list[str]      # 跟其它 skill 的 prompt 文本相似度 > 0.75(简单 difflib)

    async def curate(self) -> Result: ...
```

数据来源:
- `audit_events` filter `skill_activate` event_type(新事件,wave 2 加 hook)
- `skills` 表的 prompt 内容(overlapping 用)

### Surface(wave 4)

- CLI:`chariot skill curate`(打印 4 段建议表)
- sidecar:`curate_skills` RPC
- 桌面 `/skills` 页:三 tab
  - **Library**:全 skill 列表(builtin / db 标签 + enabled toggle button)
  - **Proposals**:audit_events filter `skill_create`,timeline 视图,每行展开
    propose payload + 当时 checkpoint id(点 → 跳 `/security#checkpoints`)
  - **Curator**:跑 `curate_skills`,显示 4 个 bucket,每条 skill 名跳到 Library

### Demo doc §7.17

把上面 wave 1-4 串成 demo:
- builtin sample → install → enable → chat with --skill → propose 新 skill →
  capability gate / checkpoint / audit 全跑通 → curator 建议归档

---

## 风险 / 决策

- **propose 误判?** —— 默认 `enabled=False`,需要人手动 enable;就算 agent 提议
  了垃圾 skill,也不立即生效;curator stale bucket 兜底
- **builtin sample 维护?** —— 3 个起步,后续按用户反馈补;不进 `external/` 子目录
  (那是 0.11.0+ 才考虑的第三方插件)
- **prompt 长度?** —— `prompt` 字段 4000 char 上限,够多数场景;太长说明该拆多
  个 skill
- **allowed_tools 跟 toolset 关系?** —— skill.allowed_tools 是 **per-call**
  filter(每次 activate 跑一次),toolset 是 **per-agent_profile** filter(装载时
  跑);两层并存,skill 更窄(取交集)。dispatch 顺序:agent_profile.toolset →
  skill.tool_filter
- **跟 reflection 关系?** —— 没冲突。skill 是"主 agent 的能力固化",critic 是
  "副 LLM 评判输出";一个 reflection turn 里如果 active 了 skill,system prompt
  会同时含 prompt bundle + skill 块,critic 收到的 trajectory 也包这些
- **B7 RL 接口?** —— wave 2 起 `audit_events.skill_activate.ok` 是天然 positive
  reward signal;B7 起做 trajectory export 时 join 这张表
- **桌面 Security 页 vs Skills 页?** —— 不合并;Security 是"运行时护栏",Skills
  是"能力管理",领域不同;但 Proposals tab 上的 checkpoint id 跳 Security 页
