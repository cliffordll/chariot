# C4 实体 ID 迁移计划

## 背景

`provider` 这一轮已经完成身份模型迁移：

- 内部真实关系使用 `provider_id`
- `provider` 自身采用 `id + slug + name`
- 历史展示字段收敛到 `provider_name_snapshot`

但仓库里仍有一批主体实体还没有稳定内部身份，仍主要依赖业务名作为主键或真实引用：

- `agent_profiles`
- `auxiliary_clients`
- `toolsets`
- `scheduled_jobs`

这些实体当前的共同问题是：

- 真实关系和展示名仍然耦合
- 重命名成本高
- repo / service / runtime / CLI / sidecar 容易继续扩散 name-as-key 语义

## 当前状态

截至当前实现，本文主线已经基本落地：

- `agent_profiles`
  - 已新增 `id`
  - `tasks.agent_profile_id` 已接通
  - `scheduled_jobs.agent_profile_id` 已接通
- `auxiliary_clients`
  - 已新增 `id`
- `toolsets`
  - 已新增 `id`
  - `toolset_members.toolset_id` 已接通
  - `agent_profiles.toolset_id` 已接通
- `scheduled_jobs`
  - 已新增 `id`
  - `job_runs.job_id` 已接通
- `tools`
  - 保持原有 `id`
  - 管理面已支持按 `name` 或 `id` 读取
  - tool call 协议层继续按 `name`

当前实现边界：

- 外部 CLI / RPC / sidecar 仍然主要以 `name` 为输入语义
- 对外查询面已逐步暴露 `id`
- repo / service 主路径已经开始兼容 `name` / `id` 双引用
- 历史/展示字段如 `conversations.agent_profile`、`trace_turns.agent_profile` 仍保留快照语义，不强行升级成真实关系

## 这轮的决策

这批实体的后续迁移，**先补 `id`，暂不引入 `slug`**。

明确约束如下：

- `provider` 保持 `id + slug + name` 模型
- 其他主体暂时只补 `id`
- 外部 CLI / RPC / UI 继续主要按 `name` 使用
- 内部真实关系逐步切到 `id`
- `name` 继续保留为用户可见名字，但不再承担长期内部关系键职责

## 为什么暂不加 slug

`slug` 不是通用必选项。

当前判断是：

- `provider` 需要稳定的人类可读引用，因此保留 `slug`
- 本文这批实体暂时没有同等强度的 `slug` 需求
- 如果现在一并引入 `slug`，会扩大 schema、repo、service、CLI、sidecar、测试的改动面

因此本阶段的目标是先解决“内部稳定身份”问题，而不是一次性推到 `id + slug + name` 的统一终态。

后续如果某一类实体出现稳定外部引用需求，再单独评估是否追加 `slug`。

## ID 类型约束

后续新增的实体 `id` 统一采用：

- 列类型：`TEXT`
- 值格式：ULID
- Python 类型：`str`

原因：

- 与当前仓库新实体主流方案一致
- 在 SQLite 下实现简单
- 排序友好
- 适合后续导入、同步、跨库合并

对应目标：

- `agent_profiles.id`: `TEXT` ULID
- `auxiliary_clients.id`: `TEXT` ULID
- `toolsets.id`: `TEXT` ULID
- `scheduled_jobs.id`: `TEXT` ULID

## 迁移优先级

### 第一梯队

#### 1. `agent_profiles`

优先级最高，原因：

- 运行时、任务系统、CLI、sidecar 依赖面广
- 已经完成 `provider_id` 收口，继续补 `id` 路径清晰
- 现在仍以 `name` 为主键，后续扩展风险最高

目标：

- 新增 `id`
- 内部关系逐步改存 `agent_profile_id`
- `name` 保留为展示/输入名

落地结果：

- 已完成 `agent_profiles.id`
- 已完成 `tasks.agent_profile_id`
- 已完成 `scheduled_jobs.agent_profile_id`
- CLI / sidecar / repo 当前兼容按 `name` 或 `id` 读取 profile

#### 2. `auxiliary_clients`

原因：

- 已完成 `provider_id` 收口
- 仍以 `name` 为主键
- `critic` / `summarizer` 等运行时绑定依赖这一层

目标：

- 新增 `id`
- 内部关系逐步改存 `auxiliary_client_id`
- `name` 保留为展示/输入名

落地结果：

- 已完成 `auxiliary_clients.id`
- 当前尚无需要继续拆出的下游真实关系表，因此先停在实体自身补 `id`
- CLI / sidecar / repo 当前兼容按 `name` 或 `id` 读取 auxiliary client

#### 3. `toolsets`

原因：

- 当前完全 name-keyed
- 已经是独立实体，不只是 join 表
- 很适合作为继 `provider` 后的下一批结构化收口对象

目标：

- 新增 `id`
- `toolset_members` 改存 `toolset_id`
- `agent_profiles.tool_profile` 最终迁成 `toolset_id`

落地结果：

- 已完成 `toolsets.id`
- 已完成 `toolset_members.toolset_id`
- 已完成 `agent_profiles.toolset_id`
- `agent_profiles.tool_profile` 仍保留为兼容展示/输入字段
- CLI / sidecar / repo 当前兼容按 `name` 或 `id` 读取 toolset

### 第二梯队

#### 4. `scheduled_jobs`

原因：

- 仍以 `name` 为主键
- 依赖面小于前三项
- 收益明确，但不如前三项紧急

目标：

- 新增 `id`
- `job_runs` 最终改存 `job_id`
- `name` 保留为展示/输入名

落地结果：

- 已完成 `scheduled_jobs.id`
- 已完成 `job_runs.job_id`
- `job_runs.job_name` 仍保留为兼容展示字段
- CLI / sidecar / repo 当前兼容按 `name` 或 `id` 读取 job

#### 5. `tools`

说明：

`tools` 现在已经有 `id`，但业务语义仍主要靠 `name`。

它和其他实体不完全一样：

- `name` 同时是工具协议名
- tool call 过程天然依赖 `name`
- 因此它不适合简单照搬 `provider` 或本文其它实体的迁移方式

后续如果要继续治理，更像是：

- 保留内部 `id`
- 明确 `name` 是协议标识
- 不强行追加 `slug`

落地结果：

- 保持 `ToolRow.id` 不变，不新增新一轮 schema 迁移
- repo / service / CLI / sidecar 的管理面已兼容按 `name` 或 `id` 读取工具
- `name` 继续作为 tool call 协议标识，不改模型调用协议

### 暂缓

#### 6. `capabilities`

原因：

- 更像系统枚举/常量，而不是普通业务实体
- 当前 `name` 主键模型可以接受
- 暂不建议为了统一风格而强行引入实体身份迁移

## 建议迁移顺序

建议按下面顺序推进：

1. `agent_profiles`
2. `auxiliary_clients`
3. `toolsets`
4. `scheduled_jobs`
5. `tools`（如果需要进一步治理）
6. `capabilities`（默认不做）

## 每一类迁移的统一实施原则

后续每次迁移都遵守相同边界：

1. 先新增 `id`，不急着删旧 `name`
2. 先把 repo / service / runtime 主路径切到 `id`
3. CLI / sidecar / RPC 保留一段时间的 `name` 输入兼容
4. 历史记录字段如果只是展示语义，不强行升级成关系
5. 等活动代码主路径完成后，再做字段清理和兼容移除

## 与 provider 方案的关系

`provider` 仍然是特殊项：

- 需要稳定人类可读引用
- 已经迁成 `id + slug + name`

本文这批实体不是 provider 的完全复制，而是采用更保守的中间方案：

- `id + name`
- 暂不加 `slug`

这条差异必须在后续实现中持续保持，不要默认把所有实体都推成 `id + slug + name`。

## 总结

后续实体身份迁移的主策略已经明确：

- 先解决“内部稳定身份”
- 不追求一次性统一成 `id + slug + name`
- 除 `provider` 外，后续主体默认只补 `id`
- `id` 统一使用 `TEXT` ULID

这能在控制改动面的前提下，继续把 name-as-key 从核心路径里清出去。

从当前落地结果看，这条迁移线已经进入“收尾阶段”：

- 主体实体补 `id` 已完成
- 主要内部真实关系改存 `*_id` 已完成
- 管理面已开始显式暴露 `id`
- 剩余工作主要是逐步把外部 surface 从“默认只提 name”过渡到“明确支持 name / id 双引用”，以及在未来适当时机清理兼容字段
