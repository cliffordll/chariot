# Provider 身份迁移计划

## 背景

当前 `provider.name` 同时承担两种职责：

- 面向人的展示名称
- 系统内部的稳定标识

这种耦合会带来几个直接问题：

- 无法安全重命名 provider
- `name` 的身份语义扩散到数据库关系、service API、CLI、RPC、trace 和配置绑定
- 后续再做身份拆分时，迁移成本会持续增大

如果这件事不尽早处理，后续代价会明显上升，主要体现在：

- schema 与数据兼容复杂度增加
- CLI / UI 行为越来越依赖历史命名
- agent / auxiliary / trace / context / prompt 等基础设施越来越难解耦
- import/export 与未来同步能力更难落地

## 目标

为 provider 建立清晰的三段式身份模型：

- `id`：ULID，不可变的内部标识
- `slug`：稳定的外部引用，用于 CLI / API / config
- `name`：可编辑的展示名称，不承担内部身份语义

核心规则：

- 所有真实内部关系统一收敛到 `provider_id`
- 历史记录统一保留 `provider_name_snapshot`
- CLI / RPC / UI 默认按 `slug` 解析
- `name` 可以修改，不破坏引用
- 默认 provider 不再挂在 provider 行上，而是迁到全局配置 `settings.default_provider_id`

## 已确定的实施决议

### 1. 默认 provider 存储位置

- 新增单行全局配置表 `settings`
- 本次先承载 `default_provider_id`
- 不复用已经删除的旧 KV settings 体系
- 不继续使用 `providers.is_default`

### 2. `provider_profile` 迁移策略

- `provider_profile` 当前是有效运行时配置入口
- 本次一起迁移
- 迁移阶段保留字段名 `provider_profile`
- 字段实际持久化值改为 `provider_id`
- 这是兼容命名，不代表长期终态

### 3. `name` 唯一性

- `provider.name` 立即允许重复
- 身份不再依赖 `name`
- 外部稳定引用由 `slug` 承担

### 4. `slug` 修改策略

- `slug` 允许修改
- 但必须通过显式操作完成，例如 `reslug`
- 不允许把 slug 修改混入普通 update

### 5. provider 删除策略

- 删除前必须迁移所有活跃引用
- 如果仍存在活跃引用，则拒绝删除
- 删除失败时必须返回明确的 blocker 列表

例如：

- 被哪些 `agent_profiles` 引用
- 被哪些 `auxiliary_clients` 引用
- 是否仍是当前 `default_provider_id`

### 6. 历史快照策略

- 历史表只保留 `provider_name_snapshot`
- 不增加 `provider_slug_snapshot`

### 7. 真实关系迁移范围

以下对象视为真实关系，必须迁到 `provider_id`：

- `provider_health`
- `auxiliary_clients`
- `agent_profiles.provider_profile`
- `trace_turns`
- `trace_provider_calls`
- `prompt_traces`
- `context_snapshots`
- `context_traces`

### 8. 弱结构 / 历史数据策略

以下位置不升级为强关系，只保留快照语义：

- `messages` 中的 provider 信息
- `memory` 中通过 link / meta 记录的 provider 信息

### 9. 兼容期引用解析顺序

兼容期统一解析顺序：

- `id -> slug -> legacy name`

### 10. 兼容期长度

- legacy name 兼容只保留 1 个版本

### 11. CLI / UI 展示规则

- provider 默认展示采用 `name (slug)`

### 12. 迁移适用范围

- 本次只做 provider
- 但 provider 方案将作为后续 `tool / toolset / prompt / agent` 的模板

## 目标模型

### providers 表

目标字段：

- `id TEXT PRIMARY KEY`：ULID
- `slug TEXT NOT NULL UNIQUE`
- `name TEXT NOT NULL`
- `type TEXT NOT NULL`
- `options TEXT NOT NULL`
- `params TEXT NOT NULL`
- `created_at`
- `updated_at`

约束说明：

- `slug` 必须是小写 ASCII
- `slug` 建议限制为 `[a-z0-9-_]+`
- `name` 允许 Unicode
- `name` 允许重复

### settings 表

本次新增单行全局配置表：

- `default_provider_id`

语义说明：

- 默认 provider 是全局应用状态
- 不属于 provider 行自身属性

## Schema 变更

### 1. providers 表扩展

新增字段：

- `id`
- `slug`
- `name`

回填规则：

- `id = 新生成 ULID`
- `slug = 旧 name`
- `name = 旧 name`

废弃字段：

- `is_default`

### 2. settings 表新增

新增单行全局配置表：

- `default_provider_id`

回填规则：

- 将旧 `providers.is_default=1` 对应 provider 解析后写入 `settings.default_provider_id`

### 3. 真实关系表新增 `provider_id`

本次需要新增 `provider_id` 的优先对象：

- `provider_health`
- `auxiliary_clients`
- `agent_profiles.provider_profile`
- `trace_turns`
- `trace_provider_calls`
- `prompt_traces`
- `context_snapshots`
- `context_traces`

### 4. 历史快照字段

对于需要保留历史展示语义的表：

- 新增 `provider_name_snapshot`

规则：

- 当前关系查找使用 `provider_id`
- 历史展示使用 `provider_name_snapshot`

## 领域模型变更

目标 `ProviderEntry`：

```python
@dataclass(frozen=True)
class ProviderEntry:
    id: str
    slug: str
    name: str
    type: str
    options: dict[str, Any]
    params: dict[str, Any]
```

规则：

- `id` 是唯一真实身份
- `slug` 是稳定外部引用
- `name` 只用于展示

## Repository 变更

`ProviderRepo` 从 name-centric 改为 id-centric。

核心方法建议：

- `get_by_id(id)`
- `get_by_slug(slug)`
- `list_entries()`
- `create(name, slug, type, options, params)`
- `update(id, ...)`
- `rename(id, new_name)`
- `change_slug(id, new_slug)`
- `delete(id)`
- `set_default(id)`
- `get_default_id()`
- `get_default()`

兼容期临时方法：

- `get_by_legacy_name(name)`
- `resolve_ref(ref)`

兼容期解析顺序固定为：

- `id -> slug -> legacy name`

约束：

- legacy name fallback 只能停留在边界层
- 不得重新成为内部主路径

## Service 层变更

`ProviderService` 负责兼容输入与统一解析。

建议 API：

- `resolve_provider(ref)`
- `resolve_provider_id(ref)`
- `show(ref)`
- `set_default(ref)`
- `rename_provider(ref, new_name)`
- `change_provider_slug(ref, new_slug)`
- `delete(ref)`

行为规则：

- 用户输入可以是 `id`、`slug` 或兼容期内的 legacy name
- service 只解析一次
- 下游统一只传 `provider_id`
- 当命中 legacy name 路径时，CLI 输出应给出废弃提示

## Runtime 与依赖变更

### Agent runtime

当前 provider 路由不能再直接绑定 provider name。

目标：

- runtime 内部传递 `provider_id`
- provider 实例查找在实际调用前完成
- 展示层仍可输出 `name` 或 `slug`

### Auxiliary

将 auxiliary 对 provider 的引用从 name 改为 id。

目标：

- `provider_entry` 迁到 `provider_id`
- CLI / UI 展示 `name (slug)`

### Trace

目标：

- 结构关系字段：`provider_id`
- 历史展示字段：`provider_name_snapshot`

要求：

- provider 重命名不能改变历史 trace 展示

### Prompt / Context

本次也按同样规则拆分：

- 结构引用使用 `provider_id`
- 历史展示使用 `provider_name_snapshot`

### Message / Memory

本次不升级为强关系。

规则：

- 保留已有快照 / 文本语义
- 不新增结构化 `provider_id`

### Health

`provider_health` 改为按 `provider_id` 建模，不再按 `provider_name`

## CLI 计划

CLI 切换为 slug-first。

建议命令：

- `provider list`
- `provider show <slug-or-id>`
- `provider add --name "Claude Prod" --slug claude-prod --type anthropic`
- `provider rename <ref> --name "Claude Production"`
- `provider reslug <ref> --slug claude-prod-cn`
- `provider use <ref>`
- `provider delete <ref>`

展示规则：

- 主展示：`name`
- 辅助展示：`slug`
- 合并展示：`name (slug)`
- `id` 作为调试引用保留支持，但不作为主展示

重要区分：

- `rename` 只改 `name`
- `reslug` 只改 `slug`

兼容期：

- 旧命令中接受 provider name 的路径临时保留
- 仅保留 1 个版本

## Sidecar / RPC 计划

RPC 不再把 `name` 混用为身份字段。

建议参数形态：

- `provider_id`
- `provider_slug`
- `provider_ref` 用于兼容包装
- `name` 仅表示展示名

建议操作：

- `show_provider(ref)`
- `add_provider(name, slug, type, options, params)`
- `update_provider(ref, ...)`
- `rename_provider(ref, name)`
- `change_provider_slug(ref, slug)`
- `use_provider(ref)`

## 前端计划

### Providers 页面

展示：

- `name`
- `slug`
- `type`
- default 状态

编辑：

- `name` 单独编辑
- `slug` 单独编辑
- `slug` 修改要显式呈现，不隐藏在普通 update 里

### Agents / Auxiliary / 其他选择器

存储：

- `provider_id`

展示：

- `name (slug)`

错误处理：

- 引用缺失时要显示明确的 broken-reference 状态
- 不能静默空白

### Traces / Logs / Conversations

- 历史渲染使用 `provider_name_snapshot`
- 不回看当前 provider name

## 迁移顺序

不要一次性硬切，采用分阶段迁移。

### Phase A：冻结语义

明确并文档化：

- `id` 是真实身份
- `slug` 是稳定外部引用
- `name` 是展示文本
- 所有真实关系必须收敛到 `provider_id`

### Phase B：Schema 扩展

新增但不立即删除旧字段：

- `providers.id`
- `providers.slug`
- `providers.name`
- `settings.default_provider_id`
- 各引用表的 `provider_id`
- 各历史表的 `provider_name_snapshot`

### Phase C：数据回填

执行回填：

- 为所有 provider 分配 ULID
- 把旧 `name` 同时写入 `slug` 和 `name`
- 将旧 name 关系解析为 `provider_id`
- 填充 `provider_name_snapshot`
- 将旧 `is_default` 转换为 `settings.default_provider_id`

### Phase D：双读双写

兼容期内：

- 新字段全部写入
- 旧字段在迁移未覆盖完前继续读取
- service / CLI / RPC 保留兼容解析

### Phase E：内部切换

重构 repo、service、runtime、RPC：

- 内部统一以 `provider_id` 为 canonical identity

### Phase F：外部切换

切换 CLI / UI / 文档：

- 默认展示与查找改为 slug-first

### Phase G：遗留清理

兼容窗口结束后：

- 删除 `providers.is_default`
- 删除 name-as-key repo 方法
- 删除 legacy name fallback
- 删除旧的 provider-name 关系字段

## 删除行为规范

删除 provider 时必须先扫描活跃引用。

如果仍存在活跃引用：

- 拒绝删除
- 返回明确 blocker 列表

至少包括：

- `agent_profiles`
- `auxiliary_clients`
- `settings.default_provider_id`
- 其他仍作为活跃配置关系的对象

历史记录不作为删除阻塞条件，历史展示依赖 snapshot 保持稳定。

## 推荐 PR 拆分

建议按 4 个阶段拆 PR：

1. schema + migrations + model 引入
2. repo / service / runtime 身份切换
3. CLI / sidecar / frontend 兼容与 UX 调整
4. 遗留清理与字段删除

原因：

- 风险更可控
- 调试更简单
- 每一步都可以单独验证

## 验证要求

必须覆盖以下测试：

- 旧 DB 到新 schema 的 migration test
- provider rename 不破坏默认 provider
- provider rename 不破坏 agent bindings
- provider rename 不破坏 auxiliary bindings
- provider rename 不改变历史 trace 展示
- slug 变更后 CLI / RPC 查找行为正确
- display name 重复时选择不歧义，因为 slug 仍唯一
- 删除 provider 时能明确报告 blocker
- 兼容期内 legacy name 仍可用
- 兼容期结束后 legacy name 正常移除

## 开放项

当前仍保留一个开放项，需在实施前再确认：

1. `provider_profile` 是否在后续单独一轮重命名为更准确的字段名

本次已明确：

- 本轮先保留字段名
- 先完成语义迁移与数据迁移

## 总结

这次迁移值得现在做。

当前 provider 模型把身份和展示绑在了一起。拆成 `id + slug + name` 后，可以：

- 降低长期迁移成本
- 让 rename 安全
- 让 CLI / UI / trace / runtime 边界更稳定
- 为后续 `tool / toolset / prompt / agent` 等实体统一身份模型提供模板

最终原则非常简单：

- `id` 用于内部身份
- `slug` 用于稳定外部引用
- `name` 用于展示
- 历史记录靠 `provider_name_snapshot` 保持上下文
