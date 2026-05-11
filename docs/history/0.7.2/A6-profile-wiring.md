# A6 Profile Wiring 设计草案

> 状态:草案,未实现,等用户决策方向后再落地
> 范围:`agent_profile` 表的 `prompt_bundle` / `tool_profile` / `provider_profile` 三个字段如何接入 `AIAgent` 实际执行
> 不在范围:`Milestone A6` 已经做完的对象模型、CRUD、UI/CLI/RPC 表面;后台 worker loop 本身

## 问题陈述

当前 A6 已经把 `agent_profile` 当成**对象模型**做完了:

- `agent_profiles` 表持久化 `name` / `role` / `prompt_bundle` / `tool_profile` / `provider_profile` / `budget` / `meta`
- 完整 CRUD:CLI / sidecar RPC / 桌面 UI 都能 list / show / create / update / delete
- `task.agent_profile` 外键引用某个 agent_profile name

但**三个 binding 字段全部是 dead string**:

- `chariot/agent/` 子目录里搜不到任何对这三个字段的引用
- `AIAgent.run_chat` / `AgentLoop` 都不读 `agent_profile`
- 实际执行还是:
  - **provider**:`req.provider_name`(请求方填)
  - **prompt**:`PromptRepo.get_active_bundle()`(全局唯一 active)
  - **tools**:`AIAgent._inject_default_tools`(全量挂载 or 显式列表)

也就是说,在 UI 上给 agent 配 `provider_profile="claude"`、`tool_profile="default"`、`prompt_bundle="research"`,**这些字段只是被存进 SQLite,对实际 task 执行没有任何作用**。

## 三个字段的不对等性

虽然名字看着对称,但三者底层基础设施成熟度差很多。

| 字段 | 现有同质实体 | 缺什么 |
|---|---|---|
| `provider_profile` | `ProviderEntry`(`name` / `type` / `options` / `params`)已存在,通过 `ProviderRegistry` 实例化、`AIAgent._providers[name]` 索引,有完整管理面 | **只缺接线**:让 `agent_profile.provider_profile` 等于 ProviderEntry.name,task 执行时按这个值挑 provider |
| `prompt_bundle` | `PromptBundleRow`(`name` / `layers` / `is_active`)已存在,有 layers 编辑、版本、激活、完整管理面 | **只缺接线**:让 `agent_profile.prompt_bundle` 等于 bundle name,task 执行时取对应 bundle 而非 active 全局 bundle |
| `tool_profile` | tools 表是**平铺**的,每条工具独立 `enabled`,**没有"一组 tool 命名集合"的概念** | **缺整套实体**:需要先定义"什么是 tool profile",再决定怎么存、怎么管,最后才谈接线 |

`provider_profile` 和 `prompt_bundle` 的解决路径几乎一样,代码量很小。`tool_profile` 是真正需要先做设计决策的那个。

## provider_profile 接线方案

### 设计意图

`agent_profile.provider_profile` 直接当 `ProviderEntry.name` 用。如果填了,task 执行就走这个 provider entry;如果为 NULL,fall back 到调用方传的 `req.provider_name`(保持现有行为)。

### 改动点

1. **task 执行入口(后台 worker / 手工 start-run)** 在构造 `ChatRequest` 时:
   - 读 `task.agent_profile` → `AgentProfile.provider_profile`
   - 若非 NULL:`req.provider_name = agent_profile.provider_profile`
   - 若 NULL:用调用方传入的或系统 default
2. **校验**:`AgentService.create_agent / update_agent` 收到 `provider_profile` 时,可选地 cross-check `ProviderRegistry` 是否存在同名 entry(给 UI 提示用,不强约束 —— 允许填一个未来才会注册的 name)
3. **UI 改进**:`Agents.tsx` 的 `provider_profile` 输入框改成 `<datalist>` 列出当前所有 provider entry name(用 `listProviders` RPC)

### 不动的

- `ProviderEntry` schema 不变
- `ProviderRegistry` 不变
- `AIAgent._providers` 索引不变
- 现有 stateful chat 路径(走 `req.provider_name`)不变

### 风险

- 一个 agent_profile 引用的 provider 被删了 → task 执行时找不到 provider → 应当 fail run with `unknown_provider` error;不应当悄悄 fall back

## prompt_bundle 接线方案

### 设计意图

`agent_profile.prompt_bundle` 直接当 `PromptBundleRow.name` 用。如果填了,task 执行时取这个 bundle 注入 system;如果为 NULL,fall back 到当前的 `get_active_bundle()`。

### 改动点

1. **`AIAgent._prepare_request`** 当前调 `PromptRepo.get_active_bundle()`。新增分支:如果 `req` 携带 `agent_profile`(新字段)且 `agent_profile.prompt_bundle` 非 NULL,改取 `PromptRepo.get_bundle_by_name(...)`
2. **`ChatRequest`** 加可选字段 `agent_profile: str | None`(只是 name,不是整个 dataclass)
3. **task 执行入口** 构造 `ChatRequest` 时填 `agent_profile=task.agent_profile`
4. **UI 改进**:`prompt_bundle` 输入框改 `<datalist>` 列已有 bundle(用 `listPromptBundles`)

### 不动的

- `PromptBundleRow` schema 不变
- `is_active` 单全局激活仍然存在;它是"未指定 agent_profile.prompt_bundle 时的兜底",**不取消**
- 现有 stateful chat 路径默认仍走 `is_active` bundle

### 跟现有 prompt 系统的关系

`is_active` 的语义微妙变化:
- 之前:"系统当前用的 prompt"
- 之后:"未绑定特定 bundle 的 task / chat 用的默认 prompt"
- 这是**收窄**不是**改变**,无 breaking

## tool_profile 设计方案(三选一)

`tool_profile` 是真正缺管理实体的字段。给三种方案对照,推荐列在最后。

### 方案 A:独立 `tool_profiles` 表 + 关联表(最重)

#### Schema

```sql
CREATE TABLE tool_profiles (
    name TEXT PRIMARY KEY,
    description TEXT,
    meta TEXT DEFAULT '{}',
    created_at DATETIME,
    updated_at DATETIME
);

CREATE TABLE tool_profile_members (
    profile_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (profile_name, tool_name),
    FOREIGN KEY (profile_name) REFERENCES tool_profiles(name) ON DELETE CASCADE
);
```

#### 配套

- `chariot/tool_profiles/` 新模块:`models.py` / `service.py`
- `chariot/repos/tool_profile_repo.py`
- sidecar RPC:`list_tool_profiles` / `get_tool_profile` / `create_tool_profile` / `update_tool_profile` / `delete_tool_profile`
- CLI:`chariot tool-profile {list, show, add, update, remove}`
- 桌面 UI:新增 Tool Profiles 页(类似 Agents 页结构)
- `AIAgent._inject_default_tools` 加分支:若 `req.agent_profile.tool_profile` 非 NULL,从 tool_profiles 解析出 tool name list,只挂这些

#### 优点

- 跟 provider entries / prompt bundles 的管理面结构对齐(三者都是命名对象 + CRUD + UI 页)
- 用户能复用同一个 profile 在多个 agent 之间
- 跟未来的"tool 版本 / tool 权限"扩展兼容

#### 缺点

- 改动面最大:新表 + migration + repo + service + RPC + CLI + UI 页
- 跟现有"tools 表平铺"概念叠加,要做心智上的层级解释
- 跟未来"工具粒度权限 / 工具用量预算"等概念的边界还未想清

### 方案 B:tools 表加 `profile` 字段(中)

#### Schema

```sql
ALTER TABLE tools ADD COLUMN profile TEXT;
-- profile = 'default' / 'web' / 'research' 等;NULL = 不属于任何 profile,fallback 全量
```

#### 配套

- `chariot/repos/tool_repo.py` 加 `list_by_profile(name)`
- `AIAgent._inject_default_tools` 加分支:若 `req.agent_profile.tool_profile` 非 NULL,只挂 tools 表里 `profile = ?` 的工具
- CLI / UI:在 Tools 页加一个 `profile` 列 + 筛选
- 不需要新表 / 新页面

#### 优点

- Schema 最小:一列
- 没有"profile 是新对象"的心智负担
- 单纯把现有 tools 表的过滤维度从"enabled bool"扩展到"enabled + profile string"

#### 缺点

- 一个 tool 只能属于一个 profile(除非 profile 字段做成 CSV / JSON list,但那就丑了)
- 没有 profile description / meta,只能用 ad-hoc 名字
- 跟"独立 profile 对象 + 多 tool 成员"那种结构不兼容,后期升级有 migration 成本

### 方案 C:`agent_profile.meta.tools` 存 JSON list(最轻)

#### Schema

不动 schema。约定 `agent_profile.meta.tools = ["read_file", "list_dir", ...]`。`tool_profile` 列保留但**不再要求等于某个 profile 名**,只是给 UI 显示用的 label。

#### 配套

- `AIAgent._inject_default_tools` 加分支:若 `req.agent_profile.meta.tools` 是非空 list,只挂这些 tool name
- UI 上 agent 编辑界面加一个 multi-select 列出已有 tools 让用户勾选
- `tool_profile` 字段的语义降级为 free-form label;或者干脆撤掉

#### 优点

- 零 schema 改动
- 跟 agent_profile 一体化:绑定关系直接挂在 agent 上
- 改动量最小

#### 缺点

- 多个 agent 要共享同一组 tool 时,得每个 agent 自己维护一份重复 list
- meta 字段被半结构化使用,长期容易混乱
- 跟 provider_profile / prompt_bundle 的"命名实体"风格不一致

### 推荐

**方案 B(tools 表加 `profile` 字段)** 是首选,理由:

- 跟 provider_profile / prompt_bundle 接线方案的"复用现有 management surface"路径一致 —— 都不引入新的顶层概念
- 改动面适中,不像 A 那样要新建一整页 UI,也不像 C 那样把结构化数据塞进 meta
- 一个 tool 只属于一个 profile 这个约束,跟"轻量分组"的目的吻合;真要做"重叠 profile"那种复杂事情,届时升级 A 也不晚

如果用户更看重"profile 是一个有 description / meta 的独立对象",选方案 A。

如果只想给某个 agent 临时绑一组工具、不想做长期可复用 profile,选方案 C。

## 跟"后台 worker loop"的耦合

profile 接线发生在 worker 把 task 转化为 `ChatRequest` 的那一步:

```text
worker claim queued task
  ↓
load task.agent_profile (string)
  ↓
SELECT * FROM agent_profiles WHERE name = ?
  ↓
build ChatRequest:
  - provider_name = agent_profile.provider_profile ?? default
  - agent_profile = agent_profile.name  # 透传 name 给 AIAgent 内部用
  ↓
AIAgent.run_chat(req)
  - _prepare_request:if agent_profile.prompt_bundle → use that bundle
  - _inject_default_tools:if agent_profile.tool_profile → filter tools
```

也就是说,**profile 接线不直接依赖 worker loop**(手工 `start-run` 也可以走同一条路径),但 worker loop 落地时必须把这条解析路径走通,否则后台执行出来的 task 全部走默认 provider / 默认 prompt / 全量 tools,等于 agent profile 字段白填。

## 推荐落地顺序

1. **先决方案**:用户在以下三件事上各拍一次板
   - `tool_profile` 选哪种方案(A / B / C)
   - `provider_profile` 是否走"接现成 ProviderEntry name"路径
   - `prompt_bundle` 是否走"接现成 PromptBundleRow name"路径
2. **改 `ChatRequest`**:加 `agent_profile: str | None` 字段(本身改动很小)
3. **改 `AIAgent`**:`_prepare_request` 和 `_inject_default_tools` 各加一个分支,读 `req.agent_profile` 解析对应 bundle / tools / provider
4. **task 执行入口**:统一一个 helper 把 task → ChatRequest,worker loop 和手工 `start-run` 都用它
5. **UI 改进**:provider_profile / prompt_bundle 输入框改 datalist,tool_profile 按选定方案展示
6. **测试**:跑 agent_profile 全字段填好的 task,验证三个 binding 都生效;跑全字段空的 task,验证 fall back 到默认行为
7. **文档**:把这份草案的"落地结果"写回 `DEVELOPMENT.md` 或 `LONGTERMPLAN.md` 对应位置

## 当前约束

- 本草案**不动代码**,只画方案
- 三个字段的接线方案彼此**独立**,可分先后落地
- 落地前要决定:在 A6 阶段内做完(扩大 A6 范围),还是作为 A6 followup(开一个 A6.x)
- 若要在 A6 内做,DEVELOPMENT.md 的"本阶段交付"得扩一条 "profile binding wiring";若作为 followup,DEVELOPMENT.md 不动,只在 task-management-demo.md 的"Remaining Work"加一条
