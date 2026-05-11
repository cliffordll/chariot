# Domain 层集中重组规划

> 状态:规划,未实现,等用户给落地动词后再动代码
> 范围:把 `task` / `prompt` / `provider` / `memory` 四个领域的业务 dataclass 与 domain service,集中到 `chariot/models/` 与 `chariot/services/` 两个目录,跟现有 `chariot/repos/` 风格对称
> 不在范围:任何业务功能改动;sidecar / CLI / 桌面 UI 表面;新增能力;DB schema

## 决策背景

前置讨论见 `docs/prompt-provider-alignment.md`(已被本规划替代)。前一版草案推荐"各领域内部分层"(`chariot/prompt/{models,service,repo}.py` 这种),最终决策走集中风格,理由:

- 项目已有强先例:`chariot/repos/`、`chariot/sidecar/services/`、`chariot/sidecar/methods/`、`chariot/cli/commands/` 全部是"按层切片,集中放"
- 跟 `chariot/repos/` 完全对称:**Repo 集中 + Service 集中 + Model 集中**,三个 surface 一致,新人一次性建立心智
- CLAUDE.md ⭐ 的攻击点是"自由函数 + 模块级状态",不是"目录如何组织";只要 service.py 里是个完整 class,放 `chariot/services/task.py` 还是 `chariot/tasks/service.py` 都对得起 ⭐
- 改动面比"全面分散"小:不动 `chariot/repos/`,只把 model 和 service 集中起来

## 现状

| 维度 | 当前位置 | 问题 |
|---|---|---|
| Task 业务 dataclass | `chariot/tasks/models.py` | 路径 OK,但跟其它领域不对称 |
| Task domain service | `chariot/tasks/service.py` | 同上 |
| Prompt 业务 dataclass | **塞在** `chariot/repos/prompt_repo.py:48-86` | 找不到家就塞进 repo |
| Prompt domain service | **不存在**,业务规则散在 `PromptRepo.activate_bundle` / `PromptRepo.record_trace` / `prompt/composer.py` 自由函数 / `sidecar/services/prompt.py` | 没 domain service 层 |
| Prompt composer | `chariot/prompt/composer.py` 模块级 `build_snapshot` / `_build_layers` 自由函数 | 反 CLAUDE.md ⭐ 第 1 / 5 条 |
| Provider 业务 dataclass | **飘在** `chariot/agent/config.py:62` | 跟 provider 领域分离,挂在 agent 配置文件里 |
| Provider domain service | **不存在**,业务规则散在 `ProviderRepo.set_default` / `copy` / `seed_if_empty` / `sidecar/services/provider.py:ProviderService` | 没 domain service 层 |
| Memory 业务 dataclass | 在 `chariot/repos/memory_repo.py`(同 prompt 问题) | 找不到家就塞进 repo |
| Memory domain service | `chariot/memory/policy.py` / `capture.py` 是部分 service 性质 | 命名风格不统一 |
| Repo | `chariot/repos/` 集中 ✅ | 不动 |

## 目标结构

```text
chariot/
  models/                # (新)集中所有 domain dataclass
    __init__.py
    task.py              # AgentProfile / Task / TaskRun / ScheduledJob /
                         # TaskKind / TaskStatus / TaskRunStatus / JobRunRecord /
                         # TaskCreate / TaskRunCreate
    prompt.py            # PromptBundleEntry / PromptVersionEntry / PromptTraceEntry /
                         # PromptLayer / PromptSnapshot
    provider.py          # ProviderEntry
    memory.py            # MemoryEntry / MemoryEventRow 业务面 mirror / MemoryPolicy
  services/              # (新)集中所有 domain service
    __init__.py
    task.py              # AgentService / TaskService / JobService
    prompt.py            # PromptService(从 PromptRepo / composer 提炼业务规则)
    provider.py          # ProviderService(从 ProviderRepo 提炼业务规则)
    memory.py            # MemoryService(承接 MemoryCaptureService 业务编排部分;
                         # 调用 chariot/memory/ 下的算法类做计算)
  repos/                 # 不动
    task_repo.py
    prompt_repo.py
    provider_repo.py
    memory_repo.py
  prompt/                # 改造后仅留无状态组合器
    composer.py          # class PromptComposer 类封装(原自由函数收成 staticmethod)
  providers/             # 多实现注册中心,保留复数命名
    base.py              # BaseProvider
    registry.py
    prober.py
    clients.py
    _sse.py
    builtin/             # 各具体 provider 实现
  agent/
    config.py            # 仅留 ChariotConfig(ProviderEntry 迁出后)
    run.py / loop.py / ... # 不动
  tasks/                 # 撤销目录,内容迁出
    (deleted; chariot/models/task.py + chariot/services/task.py 取代)
  memory/                # 保留,作为算法实现目录(类比 prompt/composer.py)
    capture.py           # MemoryCapture 类(无状态算法,候选生成 / 过滤)
    policy.py            # MemoryPolicyEngine 类(policy 应用算法);MemoryPolicy
                         # dataclass 已迁到 chariot/models/memory.py
  sidecar/               # 不动
    services/            # 现有 PromptService / ProviderService 改名为
                         # PromptApi / ProviderApi
    methods/             # 不动
```

## 工作原理 / 设计意图

### 三层切片

```text
+---------------------------+
|   surface 子系统            |
|   sidecar/services         | ← 集中,按 RPC 类型切片(prompt.py / task.py …)
|   cli/commands             |
+---------------------------+
            ↓ 调用
+---------------------------+
|   domain service            |
|   chariot/services         | ← 新增,集中,按领域切片
+---------------------------+
            ↓ 调用
+---------------------------+
|   domain model + repo       |
|   chariot/models            | ← 新增,集中,按领域切片
|   chariot/repos             | ← 现有,集中
+---------------------------+
            ↓ 数据
+---------------------------+
|   SQLAlchemy *Row           |
|   chariot/database/models   | ← 现有,集中
+---------------------------+
```

每一层都是**按领域切片**(prompt.py / task.py / provider.py / memory.py),层内集中,层与层之间清晰。

### 为什么 sidecar/services/ 已经是这个风格

`chariot/sidecar/services/` 现在已经按 RPC 类型分文件(`task.py` / `prompt.py` / `provider.py` / `chat.py` / `memory.py` / `context.py` / `tool.py`)。新的 `chariot/services/`(domain 层)是它在 domain 层的镜像。两层 service 关系是:

- sidecar `PromptApi`(改名后):薄壳,管 session、转 RpcError、序列化为 JSON
- domain `PromptService`(新建):业务规则,接收 repo 实例,在 model 上做状态机校验、跨 repo 协调

### 为什么 providers/(多实现注册中心)保留复数

`chariot/providers/` 装多个具体 provider 实现(`builtin/anthropic.py` / `builtin/mock.py` / ...),按 CLAUDE.md "装多个可插拔实现的目录 → 复数 + builtin/" 命名合理。这次重构不动它的目录命名,只把里面**飘在 `agent/config.py` 的 `ProviderEntry`** 迁到 `chariot/models/provider.py`。

`tools/` 同理:保留复数,内部 builtin/ 不动。

`chariot/tasks/` 不是多实现注册中心,只是命名 bug。重构后**整个目录撤掉**,内容迁到 `models/task.py` + `services/task.py`。

## 本阶段交付

1. `chariot/models/{task,prompt,provider,memory}.py` 四个新文件
   - 每个文件装该领域所有业务 dataclass + 状态枚举
   - 字段定义跟现状 1:1,不增不减
2. `chariot/services/{task,prompt,provider,memory}.py` 四个新文件
   - 接受 repo 实例作为构造参数(对齐 `TaskService(store)` 现状)
   - 承接现在沉在 Repo / 浮到 sidecar service / 散在自由函数的所有业务规则
3. `chariot/prompt/composer.py` 改 `PromptComposer` 类,自由函数收成 staticmethod
4. `chariot/agent/config.py` 仅留 `ChariotConfig`;`ProviderEntry` 迁出但保留 re-export 兼容
5. `chariot/tasks/` 目录撤销(rename 前先放 re-export shim,过渡一个 milestone 再删)
6. `chariot/sidecar/services/prompt.py:PromptService` → `PromptApi`
7. `chariot/sidecar/services/provider.py:ProviderService` → `ProviderApi`
8. `chariot/sidecar/services/task.py:TaskManagementService` 改名 `TaskApi`(统一后缀风格)
9. `chariot/sidecar/services/memory.py` 同步审视,如有 domain 性质方法挪到 domain service

## 本阶段不做

- DB schema 变更(`*Row` 全部不动)
- SQLAlchemy model 改动
- sidecar RPC 协议变更(输入输出 wire format 完全不变)
- CLI 命令字面 / 桌面 UI 行为变化
- 新业务功能(profile wiring / 后台 worker loop / 自动调度等)
- `chariot/repos/` 内部重写(只允许把业务规则**挪出**到 service,repo 自身的 SQL / dataclass 转换不动)
- `chariot/providers/builtin/` 各 provider 实现不动
- 跨领域依赖关系变化(谁能 import 谁,跟现在一样)

## 关键设计决策

### D1. 命名:Domain service vs Sidecar service 重名问题

| 域 | Domain layer | Sidecar surface |
|---|---|---|
| Task | `chariot/services/task.py:TaskService` | `chariot/sidecar/services/task.py:TaskApi`(改名,原 `TaskManagementService`) |
| Prompt | `chariot/services/prompt.py:PromptService`(新建) | `chariot/sidecar/services/prompt.py:PromptApi`(改名) |
| Provider | `chariot/services/provider.py:ProviderService`(新建) | `chariot/sidecar/services/provider.py:ProviderApi`(改名) |
| Memory | `chariot/services/memory.py:MemoryService`(新建) | `chariot/sidecar/services/memory.py:MemoryApi`(改名) |

约定:**`Service` 后缀 = domain;`Api` 后缀 = surface 适配(RPC 入口实体)**。

### D2. Repo 瘦身的边界

`Repo` 只做"纯数据访问":CRUD + 简单 query + 数据序列化。**业务规则**(状态校验、跨表协调、命名约束、seed 数据)挪到 domain service。

下面这些都要从 Repo 搬到 Service:

| 当前位置 | 目标位置 |
|---|---|
| `PromptRepo.activate_bundle` | `PromptService.activate_bundle`,Repo 暴露 `_set_bundle_active` 原语 |
| `PromptRepo.record_trace` | `PromptService.record_trace`,Repo 暴露纯插入 |
| `PromptRepo.render_layers_text` | 不放 Service,放 `PromptComposer.render_layers_text`(无状态,跟 composer 同栖) |
| `ProviderRepo.set_default` / `unset_default` | `ProviderService.set_default` |
| `ProviderRepo.copy` | `ProviderService.copy` |
| `ProviderRepo.seed_if_empty` | `ProviderService.seed_if_empty`(或保留 repo 极简版本,只插不查) |
| `MemoryRepo.list_relevant_entries`(若含 policy 应用) | 拆:筛选条件计算入 `MemoryService`,Repo 只接受 sanitized 参数 |

### D3. Composer 改类

`chariot/prompt/composer.py` 重写为:

```python
@dataclass(frozen=True)
class PromptLayer: ...        # 也可挪到 chariot/models/prompt.py,本规划倾向挪走

@dataclass(frozen=True)
class PromptSnapshot: ...     # 同上

class PromptComposer:
    @staticmethod
    def build_snapshot(req, *, provider_name, model, bundle_name, version,
                       memory_entries=None, memory_policy=None) -> PromptSnapshot: ...

    @staticmethod
    def _build_layers(req, ...) -> list[PromptLayer]: ...

    @staticmethod
    def render_layers_text(layers, *, existing_system, memory_entries, memory_policy) -> str: ...
```

`PromptLayer` / `PromptSnapshot` 是业务 dataclass,按本规划应挪到 `chariot/models/prompt.py`,composer.py 只剩 `PromptComposer` 类。

### D4. ProviderEntry 迁移与兼容

`ProviderEntry` 当前在 `chariot/agent/config.py:62`,被多处 import:

```text
chariot/providers/registry.py
chariot/providers/base.py
chariot/repos/provider_repo.py
chariot/sidecar/services/provider.py
chariot/cli/commands/provider.py
chariot/agent/run.py
```

迁移到 `chariot/models/provider.py` 之后,`chariot/agent/config.py` 加 re-export shim:

```python
# chariot/agent/config.py(过渡期)
from chariot.models.provider import ProviderEntry as ProviderEntry  # re-export

@dataclass(frozen=True)
class ChariotConfig:
    providers: list[ProviderEntry]
    ...
```

按 CLAUDE.md "非破坏优先",一个 milestone 之后所有调用方迁完再删 re-export。

### D5. `chariot/tasks/` 撤销

`chariot/tasks/__init__.py` 当前 export `AgentProfile / Task / TaskRun / ScheduledJob / AgentService / TaskService / JobService` 等。

撤销分两步:

1. **第一步**:`chariot/tasks/__init__.py` 改为纯 re-export shim:
   ```python
   from chariot.models.task import (
       AgentProfile, Task, TaskRun, ScheduledJob,
       TaskCreate, TaskKind, TaskRun, TaskRunCreate, TaskRunStatus, TaskStatus, JobRunRecord,
   )
   from chariot.services.task import AgentService, JobService, TaskService
   ```
   所有 `from chariot.tasks import ...` 依然能用。
2. **第二步**(下个 milestone):调用方迁到 `chariot.models.task` / `chariot.services.task`,删 `chariot/tasks/` 目录。

### D6. `chariot/memory/` 的处理

`memory` 当前有:

- `chariot/memory/policy.py:MemoryPolicy`(dataclass,policy 规则容器)
- `chariot/memory/capture.py:MemoryCaptureCandidate`(dataclass,候选项)
- `chariot/memory/capture.py:MemoryCaptureService`(业务 service)
- `chariot/repos/memory_repo.py:MemoryRepo`(数据访问)

**采用方案 b:`chariot/memory/` 目录保留作为算法实现目录**(类比 `chariot/prompt/composer.py` 留下来的角色),拆分如下:

| 当前位置 | 目标位置 | 说明 |
|---|---|---|
| `chariot/memory/policy.py:MemoryPolicy`(dataclass 部分) | `chariot/models/memory.py` | 业务 dataclass 跟其它领域一样集中到 models |
| `chariot/memory/capture.py:MemoryCaptureCandidate`(dataclass) | `chariot/models/memory.py` | 同上 |
| `chariot/memory/capture.py:MemoryCaptureService` 中"业务编排 / 跨 repo 协调" | `chariot/services/memory.py:MemoryService` | 持 repo 引用、写库的部分提到 domain service |
| `chariot/memory/capture.py:MemoryCaptureService` 中"候选生成 / 过滤算法 / 纯计算" | **留在** `chariot/memory/capture.py:MemoryCapture` 类 | 改类封装(对齐 ⭐ 第 1 / 5 条),无状态,被 `MemoryService` 调用 |
| `chariot/memory/policy.py` 中"policy 应用规则 / 算法" | **留在** `chariot/memory/policy.py:MemoryPolicyEngine` 类(或留作 `MemoryPolicy` 的方法,看体量) | 同上,算法层 |
| `chariot/repos/memory_repo.py:MemoryRepo` | 不动 | repo 集中保留 |

**memory/ 目录最终角色**:**算法实现层**,装无状态计算单元(类比 `prompt/composer.py`)。
**services/memory.py 角色**:domain service,持 repo 引用 + 调 memory/ 里的算法类做计算 + 把结果落库。

这条路径让 memory 跟 prompt 风格一致(两者都是"实现细节目录 + domain service 调用"模式),区别只在 prompt 的"实现"是 composer 组合,memory 的"实现"是 capture / policy 算法。

## 执行顺序

按风险递增 + 每步可独立 commit + 可回滚:

1. **第一步(prep):新建空目录 + re-export shim**
   - 创建 `chariot/models/__init__.py`、`chariot/services/__init__.py`
   - 此 commit 不影响任何运行
2. **第二步(`task` 领域,试点)**
   - 把 `chariot/tasks/models.py` 内容搬到 `chariot/models/task.py`
   - 把 `chariot/tasks/service.py` 内容搬到 `chariot/services/task.py`
   - `chariot/tasks/__init__.py` 改 re-export shim
   - 更新 `chariot/sidecar/services/task.py` / `chariot/cli/commands/task.py` / `chariot/repos/task_repo.py` 的 import 路径(从 `chariot.tasks` → `chariot.models.task` / `chariot.services.task`),也可以暂时不动(走 shim)
   - 跑全套 ruff / pyright / pytest;sidecar 烟测
3. **第三步(`prompt` 领域)**
   - 新建 `chariot/models/prompt.py`:把 `PromptBundleEntry` / `PromptVersionEntry` / `PromptTraceEntry` 从 `chariot/repos/prompt_repo.py` 搬过来;把 `PromptLayer` / `PromptSnapshot` 从 `chariot/prompt/composer.py` 搬过来
   - 新建 `chariot/services/prompt.py:PromptService`,把 `PromptRepo` 的业务方法搬过来
   - `chariot/prompt/composer.py` 改 `PromptComposer` 类
   - `chariot/sidecar/services/prompt.py:PromptService` 改名 `PromptApi`,内部改调 domain `PromptService`
   - 跑全套静态检查与烟测
4. **第四步(`provider` 领域)**
   - 新建 `chariot/models/provider.py`,把 `ProviderEntry` 从 `chariot/agent/config.py` 搬过来
   - `chariot/agent/config.py` 加 re-export shim
   - 新建 `chariot/services/provider.py:ProviderService`,搬 `ProviderRepo` 业务方法
   - `chariot/sidecar/services/provider.py:ProviderService` 改名 `ProviderApi`
   - 跑全套静态检查与烟测
5. **第五步(`memory` 领域)**
   - 新建 `chariot/models/memory.py`,搬 `MemoryPolicy` / `MemoryCaptureCandidate` 等 dataclass(从 `chariot/memory/policy.py` 和 `chariot/memory/capture.py`)
   - 新建 `chariot/services/memory.py:MemoryService`,把 `MemoryCaptureService` 中"业务编排 / 跨 repo 写库"部分搬过来
   - `chariot/memory/capture.py` 留下"候选生成 / 过滤"无状态算法,改 `MemoryCapture` 类封装
   - `chariot/memory/policy.py` 留下 policy 应用规则算法,改 `MemoryPolicyEngine` 类封装(若体量太小可直接挂为 `MemoryPolicy` dataclass 的 @staticmethod / 方法,跟着 dataclass 一起迁,这种情况下 policy.py 整体撤销)
   - `chariot/sidecar/services/memory.py:MemoryService` 改名 `MemoryApi`
   - 跑全套静态检查与烟测
6. **第六步:清理 deprecation**
   - 等所有调用方 import 迁完,撤销 `chariot/tasks/__init__.py` re-export shim,删 `chariot/tasks/` 目录
   - 撤销 `chariot/agent/config.py` 里的 `ProviderEntry` re-export
   - `chariot/memory/` 目录按 D6 b 方案保留;无需在本步处理
7. **第七步(`context` 领域,补做)**
   - 原规划遗漏 context 领域 —— 它和 prompt/memory 形态一致,落地后补一步
   - 新建 `chariot/models/context.py`:把 `ContextSlice` / `ContextSnapshot` 从 `chariot/context/composer.py` 搬过来;把 `ContextSnapshotEntry` / `ContextTraceEntry` 从 `chariot/repos/context_repo.py` 搬过来
   - `chariot/context/composer.py` 模块级 `build_snapshot` + `_build_slices` 收成 `ContextComposer` 类的 classmethod / staticmethod;消除 CLAUDE.md ⭐ red flag(模块级自由函数 + 下划线 helper)
   - 新建 `chariot/services/context.py:ContextService`,薄封 `ContextRepo`(list/get snapshots & traces / record / inspect)
   - `chariot/sidecar/services/context.py:ContextService` 改名 `ContextApi`;`chariot/sidecar/methods/context.py` 跟进
   - `chariot/agent/run.py` / tests 的 `build_snapshot` 调用点改走 `ContextComposer.build_snapshot`
   - 跑全套静态检查与烟测

## 验收标准

- `chariot/models/{task,prompt,provider,memory,context}.py` 五文件存在,内容是该领域全部业务 dataclass(第七步补 context)
- `chariot/services/{task,prompt,provider,memory,context}.py` 五文件存在,内容是该领域全部 domain service 类(第七步补 context)
- `chariot/repos/{task,prompt,provider,memory,context}_repo.py` 不再持有业务规则(状态机校验 / 跨表协调 / 命名约束 / seed)
- `chariot/{prompt,context}/composer.py` 内无模块级自由函数,无 `_xxx` 模块级 helper(都成为 `PromptComposer` / `ContextComposer` 的 staticmethod)
- `chariot/agent/config.py` 仅含 `ChariotConfig`,`ProviderEntry` re-export 已撤
- `chariot/tasks/` 目录撤销或仅剩 re-export shim
- `chariot/sidecar/services/{prompt,provider,memory,context}.py` 类名统一改 `XxxApi` 后缀(`PromptApi` / `ProviderApi` / `MemoryApi` / `ContextApi`)
- ruff / pyright / pytest 全套通过
- sidecar / CLI / 桌面 UI 三条 surface 行为不变,现有测试不需要改预期

## 验收方法

每一步独立验证:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright chariot/
uv run pytest -q
```

外加 sidecar 烟测:

```powershell
uv run chariot agent list
uv run chariot task list
uv run chariot job list
uv run chariot provider list
```

桌面 UI:三个页面分别能 list / show / 编辑 / 删除,行为跟重构前一致。

## 当前约束 / 风险

- **R1.** 大量 import 路径变动 — 用 re-export shim 渐进迁移,降低单 commit 风险面
- **R2.** Sidecar service 改名(`PromptService` → `PromptApi` 等)— 影响 `chariot/sidecar/methods/{prompt,provider,memory}.py` 里的类型引用,要同步更新
- **R3.** 业务规则从 Repo 迁出时,**SQLAlchemy 事务边界**变化:原来 repo 内一个方法 + 一次 session 调用;迁后 service 方法可能调多个 repo 方法 + 共用同一 session。要保证 service 方法整体仍在一个事务内 — 跟现有 `TaskService` 风格一致(由 sidecar 层 / surface 层管 session)
- **R4.** 这是个跨领域、跨 surface 的中等手术,**不建议混在功能开发里做** — 建议作为独立 milestone(暂命名 `Refactor-1: domain layer centralization`),与 `A6` 后续功能(后台 worker / profile wiring / 自动调度)解耦
- **R5.** `chariot/repos/` 长期保留(本规划不动它)— 长远看可能再起一份"撤 repos/"的草案,但**不在本规划范围内**
