# Rules

> 这份文档只描述长期规则，不描述当前阶段做什么。  
> 阶段设计和执行顺序看 `DEVELOPMENT.md`。

## 1. 命名原则

### 1.1 一个概念一个主名

同一个概念只保留一个主名，其他叫法只允许留在兼容层。

当前主名统一为：

- `provider`
- `convo`
- `message`
- `tool`
- `skill`
- `memory`
- `checkpoint`
- `audit`
- `eval`

### 1.2 `provider` 和 `model` 的区别

- `provider`：本地注册的提供方入口
- `model`：真正发给上游 API 的模型 ID

新代码里：

- 业务主概念统一使用 `provider`
- `model` 只保留给上游模型名

### 1.3 `convo` 作为主名

仓库里“多轮对话单元”统一使用 `convo`。

统一使用：

- `ConvoRow`
- `ConvoRepo`
- `convo_id`
- `list_convos`
- `get_convo`

`conversation` 只允许留在兼容层和历史文档。

## 2. 对象分层命名

统一按层次命名：

- `*Row`：数据库对象
- `*Repo`：数据访问层
- `*Config`：配置对象
- `*Registry`：注册表或构建中心
- `*Manager`：生命周期或协调逻辑
- `*Service`：业务编排层
- `*Runtime`：运行时实例或状态

## 3. 缩略词规则

### 3.1 保留的稳定缩写

只保留这些稳定缩写：

- `convo`
- `repo`
- `rpc`
- `cli`
- `api`
- `db`

### 3.2 不再新增自造缩略词

原则上不再新增：

- `prov`
- `sk`
- `tg`
- `rt`
- `ctxsvc`

能写全就写全，优先可读，不优先短。

## 4. 动词规则

优先使用：

- `get_*`
- `list_*`
- `create_*`
- `update_*`
- `delete_*`
- `enable_*`
- `disable_*`
- `reload_*`
- `invalidate_*`
- `run_*`
- `stream_*`
- `execute_*`
- `parse_*`
- `normalize_*`
- `validate_*`
- `serialize_*`
- `deserialize_*`

避免继续扩散：

- `edit_*`
- `fetch_*`
- `load_*` 作为主业务接口名

## 5. `run` 的使用规则

`run` 只保留给少数顶层流程入口。

适合保留：

- `AIAgent.run_chat`
- 类似“运行一次完整流程”的入口

不适合泛用在局部动作里。  
局部动作优先改成：

- `stream_*`
- `execute_*`
- `normalize_*`
- `validate_*`

## 6. 低收益重命名处理规则

以下名字短期不单独发起全仓清理：

- `bootstrap`
- `reserve`
- `append_message`
- `persist_*`
- `with_advisory_lock`

只有当三个条件同时满足时，才顺手改：

1. 当前文件本来就会改
2. 改名不会扩大很多调用面
3. 改完后语义明显更清楚

## 7. 新模块命名建议

### 7.1 Memory

- `MemoryRow`
- `MemoryRepo`
- `MemoryService`

### 7.2 Skills

- `SkillRow`
- `SkillRepo`
- `SkillRegistry`
- `SkillService`

### 7.3 Eval

- `EvalRunRow`
- `EvalCaseRow`
- `EvalRepo`
- `EvalService`

### 7.4 Audit

- `AuditEventRow`
- `AuditRepo`
- `AuditService`

### 7.5 Checkpoints

- `CheckpointRow`
- `CheckpointRepo`
- `CheckpointManager`

## 8. 当前规则结论

后续新代码统一按下面三条执行：

1. 主概念统一叫 `provider`、`convo`
2. 规则上不再新造缩略词
3. 命名和结构一起收，不做脱离模块的大扫除
