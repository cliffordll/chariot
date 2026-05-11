# Prompt / Provider 系统结构对齐草案

> ⚠ **已被 `docs/domain-centralization.md` 取代,方向已收口为集中风格,本文不再代表当前规划**
> 保留作历史草案:本草案推荐"各领域内部分层"(`chariot/prompt/{models,service}.py` 等);经讨论后用户决定走集中风格(`chariot/models/{prompt,provider,...}.py` + `chariot/services/{prompt,provider,...}.py`),理由见新规划开头"决策背景"一节
>
> 原始草案内容如下,仅供历史参考。
>
> 状态:草案,未实现,等用户决策方向后再落地
> 范围:`chariot/prompt/` 与 `chariot/providers/` 两个领域的 backend 结构,对齐到 `chariot/tasks/`(A6 阶段树立的样板)
> 不在范围:任何业务功能改动;sidecar / CLI / UI surface 表面;新增能力

## 问题陈述

`Milestone A6` 做 task 系统时遵循 `CLAUDE.md` ⭐ "封装与内聚最高优先级",留下一份相对干净的结构:

```text
chariot/tasks/
  models.py          # 业务 dataclass:AgentProfile / Task / TaskRun / ScheduledJob + 状态枚举
  service.py         # domain service:AgentService / TaskService / JobService
chariot/repos/
  task_repo.py       # 纯数据访问:TaskRepo
chariot/sidecar/services/
  task.py            # 薄 RPC 适配:TaskManagementService
chariot/sidecar/methods/
  task.py            # RPC 入口
```

早期(0.7.1 之前)做 prompt / provider 时还没建立这套规范,留下两类技术债:

### Prompt 系统现状

```text
chariot/prompt/
  composer.py        # ❌ 模块级自由函数 build_snapshot / _build_layers,反 ⭐ 第 1 / 5 条
                     #    业务 dataclass PromptLayer / PromptSnapshot 也散在这里
chariot/repos/
  prompt_repo.py     # ❌ 业务 dataclass PromptBundleEntry / PromptVersionEntry /
                     #    PromptTraceEntry 跟 PromptRepo 同文件;
                     # ❌ PromptRepo 内 render_layers_text / activate_bundle /
                     #    activate_version / record_trace 等承担 domain 职责
chariot/sidecar/services/
  prompt.py          # PromptService 被迫吸纳一些 domain 职责
chariot/sidecar/methods/
  prompt.py          # RPC 入口
```

### Provider 系统现状

```text
chariot/providers/
  base.py            # BaseProvider abstract
  registry.py        # ProviderRegistry(builder)
  prober.py          # 探活
  clients.py         # ClientCache(httpx 进程级 LRU)
  _sse.py            # SseParser
  builtin/           # 具体 provider 实现
                     # 注:这个目录按 CLAUDE.md "装多个可插拔实现 → 复数 + builtin/"
                     # 命名规则是对的,但因此目录被占用,domain service 无处安放
chariot/agent/
  config.py          # ❌ ProviderEntry 业务 dataclass 飘在这里(只因历史上跟
                     #    ChariotConfig 一起出现);ChariotConfig 才该在 agent/
chariot/repos/
  provider_repo.py   # ❌ PrividerRepo 内 set_default / unset_default / copy /
                     #    seed_if_empty 等承担 domain 职责
chariot/sidecar/services/
  provider.py        # ProviderService 同样吸纳部分 domain
chariot/sidecar/methods/
  provider.py        # RPC 入口
```

## 三处不对等的具体表现

### 1. 业务 dataclass 没有独立模块

`tasks/models.py` 把 `AgentProfile` / `Task` 这种业务对象单独成文件,跟 SQLAlchemy `*Row` 解耦。

- prompt:`PromptBundleEntry` 等三个 dataclass 跟 `PromptRepo` 同文件(`repos/prompt_repo.py:48-86`)
- provider:`ProviderEntry` 跟 `ChariotConfig` 同文件(`agent/config.py:62-77`),位置上跟 agent 顶层配置混在一起,跟它本来该归属的 providers 领域分离

### 2. Domain service 缺失

`tasks/service.py` 里 `TaskService` 一类承担:
- 状态机校验(`task.with_status` 抛 `invalid transition`)
- 跨 repo 协调(`start_task_run` 先 update task → 再 create run)
- 业务规则封装(`require_task` / `require_run` 统一 not-found)

prompt / provider 都没有这种 domain service 类。同等职责被分散到三处:

- **沉到 Repo**:`PromptRepo.activate_bundle` / `PromptRepo.record_trace` / `ProviderRepo.set_default` / `ProviderRepo.copy` / `ProviderRepo.seed_if_empty` —— 这些是业务规则,不是纯数据访问
- **散在自由函数**:`prompt/composer.py:build_snapshot` 是 domain 行为而非纯 utility
- **浮到 Sidecar service**:本该是 RPC 适配薄壳的 `PromptService` / `ProviderService` 被迫吸纳 domain 职责

### 3. 自由函数 / 下划线 helper 反 ⭐

`chariot/prompt/composer.py`:

- 第 32 行 `def build_snapshot(...)` — 模块级自由函数
- 第 72 行 `def _build_layers(...)` — 下划线 helper

正中 `CLAUDE.md` ⭐ 第 5 条反面样板:"`_build_xxx` / `_parse_xxx` 这样的下划线起头模块级函数,基本都属于某个类"。

## 目标结构

把两个系统都重构到 task 同款结构:

### 目标:Prompt

```text
chariot/prompt/
  models.py          # ✅ 业务 dataclass:PromptBundleEntry / PromptVersionEntry /
                     #    PromptTraceEntry / PromptLayer / PromptSnapshot
  service.py         # ✅ domain service:PromptService(状态机 / 跨 repo 协调 /
                     #    业务规则封装)
  composer.py        # ✅ class PromptComposer:把 build_snapshot / _build_layers
                     #    收成方法
chariot/repos/
  prompt_repo.py     # ✅ 只剩 PromptRepo,业务方法瘦身(activate_* / record_trace
                     #    等业务规则挪到 service)
chariot/sidecar/services/
  prompt.py          # ⚠ 注意:这里也叫 PromptService 会跟 domain service 重名;
                     #    建议改 PromptManagementService(对齐 task:
                     #    sidecar/services/task.py:TaskManagementService)
chariot/sidecar/methods/
  prompt.py          # 不动
```

### 目标:Provider

```text
chariot/providers/
  base.py            # 不动
  registry.py        # 不动
  prober.py          # 不动
  clients.py         # 不动
  _sse.py            # 不动
  builtin/           # 不动
  models.py          # ✅ 业务 dataclass:ProviderEntry
                     # 注:ProviderEntry 从 chariot/agent/config.py 迁出
  service.py         # ✅ domain service:ProviderService(default 管理 / copy /
                     #    seed / 路由策略)
chariot/agent/
  config.py          # ✅ 只剩 ChariotConfig;ProviderEntry import from
                     #    chariot.providers.models
chariot/repos/
  provider_repo.py   # ✅ ProviderRepo 业务方法瘦身
chariot/sidecar/services/
  provider.py        # ⚠ 同样重名问题;建议改 ProviderManagementService
chariot/sidecar/methods/
  provider.py        # 不动
```

## 关键设计决策

### D1. Sidecar service 重命名

`tasks` 已经用 `TaskManagementService`,跟 domain 层未来可能出现的 `TaskService` 区分。本次重构后:

- `sidecar/services/prompt.py:PromptService` → `PromptManagementService`
- `sidecar/services/provider.py:ProviderService` → `ProviderManagementService`
- domain 层新建 `PromptService` / `ProviderService`

理由:`Management` 后缀表 "RPC 适配 + session 管理",不承担业务规则;不带后缀的 service 是真正的 domain。

### D2. Repo 瘦身的边界

`Repo` 只做"纯数据访问":CRUD + 简单 query + 数据序列化。**业务规则**(状态校验、跨表协调、命名约束)挪到 domain service。

例子:

| 当前位置 | 内容 | 目标位置 |
|---|---|---|
| `PromptRepo.activate_bundle` | 同时把别的 bundle `is_active=0` 再把目标 `is_active=1` | `PromptService.activate_bundle`,Repo 只暴露 `_set_bundle_active` 这种基础原语 |
| `PromptRepo.record_trace` | 跨 trace / version 关联 | `PromptService.record_trace`,Repo 暴露纯插入 |
| `ProviderRepo.set_default` / `unset_default` | "全局只能一个 default" 业务约束 | `ProviderService.set_default`,Repo 暴露纯写 |
| `ProviderRepo.copy` | 命名生成 + 复制 | `ProviderService.copy` |
| `ProviderRepo.seed_if_empty` | 业务初始数据 | `ProviderService.seed_if_empty`(或单独保留 repo seed 但只做最纯净的 insert) |

`PromptRepo.render_layers_text` 是个特例 —— 它跟 prompt 业务关系紧密,但又不依赖 session。建议挪到 `PromptComposer` 类里当 `@staticmethod`。

### D3. Composer 改类封装

`prompt/composer.py` 改成:

```python
@dataclass(frozen=True)
class PromptLayer: ...

@dataclass(frozen=True)
class PromptSnapshot: ...

class PromptComposer:
    @staticmethod
    def build_snapshot(req, *, provider_name, model, bundle_name, version,
                       memory_entries=None, memory_policy=None) -> PromptSnapshot: ...

    @staticmethod
    def _build_layers(req, *, provider_name, model,
                      memory_entries, memory_policy) -> list[PromptLayer]: ...

    @staticmethod
    def render_layers_text(layers, *, existing_system, memory_entries, memory_policy) -> str: ...
```

不写实例,因为 composer 没有状态。`@staticmethod` 是 ⭐ "能 staticmethod 就不要模块级"的体现。

> 也可考虑 `PromptLayer` / `PromptSnapshot` 挪到 `prompt/models.py`,composer.py 只剩 `PromptComposer` 类。但 dataclass 跟 composer 配套使用、不在别处用,放一起也合理。**两选一,本草案不强推一边**。

### D4. ProviderEntry 的迁移成本

`ProviderEntry` 当前在 `chariot/agent/config.py`,被很多地方 import:

```text
chariot/providers/registry.py
chariot/providers/base.py(?)
chariot/repos/provider_repo.py
chariot/sidecar/services/provider.py
chariot/cli/commands/provider.py
chariot/agent/run.py
... 以及 ChariotConfig 内部持有
```

迁移到 `chariot/providers/models.py` 之后,`chariot/agent/config.py` 保留 re-export(`from chariot.providers.models import ProviderEntry as ProviderEntry`)做向后兼容,所有 import 路径渐进迁移。

按 `CLAUDE.md` "非破坏优先",rename 而非删旧建新;`agent/config.py` 加 deprecation 注释。

`ChariotConfig`(顶层配置容器)留在 `agent/config.py` 不动 —— 它确实属于 agent 配置层。

### D5. 命名重名的解决

`tasks/service.py` 已经叫 `service.py`(domain 层),`sidecar/services/task.py` 文件名也是 `task.py`,不存在文件名冲突。**问题在类名 `Service`**:

- `chariot/prompt/service.py:PromptService`
- `chariot/sidecar/services/prompt.py:PromptManagementService`

类名不同,文件路径不同,IDE / 导入语法上能区分。本草案选择"用不同类名做语义区分"而非"全部 import 加 alias"。

## 跟功能开发的耦合

**本重构不引入也不阻塞任何功能改动**:

- 不影响 A6 后台 worker loop:worker 调 domain service,反而比当前直接调 repo 更干净
- 不影响 `A6-profile-wiring.md` 三个 profile 接线:wiring 的下游就是 `PromptService.get_bundle_by_name` / `ProviderService.get_entry` 这种 domain 方法,有正式的 domain service 之后接线更顺
- 不影响现有 sidecar RPC 协议:RPC 输入输出格式不变,只是内部实现走 domain service 多一跳

## 风险与不动的

### 风险

- **R1. 渐进重构期间双 service 同名容易混淆**:`PromptService` 和 `PromptManagementService` 共存几个 commit 时,review 容易看错。**缓解**:一次重构一个领域,合并后再开下一个
- **R2. SQLAlchemy session 边界变了**:repo 方法当前在 session 内被调用,挪到 service 后 service 也要拿 session。**缓解**:对齐 task 的 pattern,service 接受 repo 实例作为参数,session 由调用方(sidecar / CLI)管理
- **R3. ProviderEntry import 路径变化**:外部依赖(如果有)需要更新。**缓解**:`agent/config.py` 保留 re-export

### 不动的

- DB schema 不动
- SQLAlchemy `*Row` 类不动
- sidecar RPC 协议不动
- CLI 命令字面与 UI 不动
- `chariot/providers/builtin/` 各具体实现不动
- `chariot/prompt/` 不新增子目录(`builtin/` 不引入,因为 prompt 不是"多实现"领域)

## 推荐落地顺序

按"一个领域一次重构 + 每步可验收"原则:

1. **第一步(prompt 系统重构)**
   1. 新建 `chariot/prompt/models.py`,把 dataclass 从 `repos/prompt_repo.py` 和 `prompt/composer.py` 集中过来
   2. `prompt/composer.py` 改 `PromptComposer` 类封装
   3. 新建 `chariot/prompt/service.py:PromptService`,把 `PromptRepo` 的业务方法搬过来,`PromptRepo` 只剩纯数据访问
   4. `chariot/sidecar/services/prompt.py:PromptService` 改名 `PromptManagementService`,内部调 domain `PromptService`
   5. 跑全套 ruff / pyright / pytest;sidecar / CLI 烟测
2. **第二步(provider 系统重构)**
   1. 新建 `chariot/providers/models.py`,把 `ProviderEntry` 从 `agent/config.py` 迁过来;`agent/config.py` 加 re-export 兼容
   2. 新建 `chariot/providers/service.py:ProviderService`,搬 `ProviderRepo` 的业务方法过来
   3. `chariot/sidecar/services/provider.py:ProviderService` 改名 `ProviderManagementService`
   4. 跑全套静态检查与烟测
3. **第三步(文档收尾)**
   1. 更新 `docs/DESIGN.md`(若有领域分层描述)
   2. 这份草案归档到 `docs/history/<version>/`(看落地时的版本号)
   3. `DEVELOPMENT.md` 不动(本重构不属于"当前阶段"功能交付)

## 验收标准

- `chariot/prompt/` 和 `chariot/providers/` 各自有 `models.py` + `service.py`
- 业务 dataclass 不再散在 repo 或 agent 配置里
- `PromptRepo` / `ProviderRepo` 不再持有业务规则(状态机校验 / 跨表协调 / 命名约束)
- `prompt/composer.py` 内无模块级自由函数,且无下划线起头的 helper(都成为 `PromptComposer` 的方法)
- 全套静态检查(ruff / pyright / pytest)通过
- sidecar RPC / CLI / 桌面 UI 三条 surface 行为不变
- 现有测试不需要改预期(实现重构,接口不变)

## 当前约束

- 本草案**不动代码**,只画方案
- 两个领域的重构**彼此独立**,可分别落地,不必合并
- 落地前要明确:作为独立 milestone(`Milestone Refactor-1` 之类),还是夹在功能开发中的"碎片化重构"。本草案推荐**独立 milestone**,理由是涉及面广、跨多文件,夹带功能开发容易混淆 commit 责任。
