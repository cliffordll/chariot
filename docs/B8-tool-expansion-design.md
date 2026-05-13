# B8 - 工具扩展与管理（Tool Expansion & Management）
> Milestone: `feat/0.8.7-toolmngn`
> 范围：扩充内置工具种类 + 支持自定义工具 + 完整 CRUD 管理
> 设计前提：
> - 现有 `tools` 表已有 `name/type/enabled/options`，本版补 `source/description/custom_type`
> - `ToolRegistry` 是代码级注册中心，`ToolEntry` 是 DB 配置对象
> - B5 guardrails 已接入 `tool_call_pre/post`
> - 模块归属：`chariot/tools/` 内新增 builtin/custom 支持

---

## 0. 为什么现在做

当前 chariot 的工具层存在几个明显缺口：

1. 有读无写：agent 能读文件但不能优雅、安全地写文件
2. 缺文件搜索：大量代码/文档定位只能退回 `shell_exec`
3. 缺网页搜索：`http_get` 只能访问已知 URL，不能主动找信息
4. 缺任务跟踪：复杂任务没有 todo list，agent 容易遗漏子目标
5. 缺 git 只读集成：代码任务没有结构化 `git status/diff` 能力
6. 缺扩展机制：用户想接自有 API 或命令，只能改代码重部署

不做：

- 动态 codegen 工具
- 任意脚本执行引擎
- 爬虫 / spider 平台

---

## 1. Wave 拆分

| Wave | 内容 | 交付 |
|---|---|---|
| 1 | 内置工具扩充：`write_file`、`edit_file`、`search_files`、`web_search`、`web_extract`、`todo`、`git_status` | 12 个 builtin 工具可用 |
| 2 | 自定义工具系统：`http_custom` / `shell_custom`，DB 持久化，注册与加载 | 用户可注册自定义工具 |
| 3 | 管理面：CLI + sidecar RPC + 桌面 `/tools` 页面 | 工具可完整管理 |

> 顺序约束：先补能力，再补扩展，最后补管理面。

---

## 2. Wave 1：内置工具扩充

### 2.1 工具清单

| 工具名 | 能力 |
|---|---|
| `write_file` | 写文件，支持覆盖/追加 |
| `edit_file` | 基于 `old_string -> new_string` 的定点编辑 |
| `search_files` | 递归搜索文件内容 |
| `web_search` | Web 搜索 |
| `web_extract` | URL -> 可读 markdown |
| `todo` | session 内任务列表 CRUD |
| `git_status` | 结构化 git 仓库状态 |

### 2.2 关键设计点

#### write_file

- 默认 `overwrite`
- 可选 `append`
- 默认不允许写出 cwd
- 走 `file_write_outside_cwd` / `file_write_secrets`

#### edit_file

- 输入为 `path/old_string/new_string`
- 默认只允许替换 1 处
- 未找到或多处命中时返回 `is_error=True`
- 不做通用 patch 语法

#### search_files

- 支持递归目录搜索
- 支持扩展名过滤
- 支持纯文本和 regex
- 只读，不新增写类 guardrail

#### web_search

- 后端优先级：Tavily > DuckDuckGo
- 返回结构化结果：`[{title, url, snippet}]`

#### web_extract

- `httpx GET` 拉取
- 正文提取优先 `trafilatura`
- 支持 `allowed_domains`

#### todo

```python
class TodoTool(BaseTool):
    """Session 内任务列表管理。"""

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 操作 input["_todo_store"]
        ...
```

`todo` 的关键约束：

- `TodoStore` 是 per-session runtime state，不持久化到 DB
- `TodoTool` 不直接依赖 `AIAgent` 实例
- 由 `ToolExecutionService` 在每次调用前注入 runtime-only 的 `_todo_store`
- 同一 session 的多次 `todo` 调用必须共享同一个 `_todo_store`
- 不同 session 之间不得串状态

#### git_status

- 只读工具
- 不允许修改仓库
- 结构化输出 `branch/ahead_behind/modified/untracked/diff_summary`

### 2.3 模块布局

```text
chariot/tools/builtin/
├── read_file.py
├── list_dir.py
├── shell_exec.py
├── http_get.py
├── propose_skill.py
├── write_file.py
├── edit_file.py
├── search_files.py
├── web_search.py
├── web_extract.py
├── todo.py
└── git_status.py
```

### 2.4 依赖

新增：

```toml
dependencies = [
  "trafilatura>=1.6",
  "duckduckgo-search>=5.0",
]
```

### 2.5 Guardrails

| 工具 | 规则 |
|---|---|
| `write_file` | `file_write_outside_cwd`, `file_write_secrets` |
| `edit_file` | `file_write_outside_cwd`, `file_write_secrets` |
| `search_files` | 无新增 |
| `web_search` | 无新增 |
| `web_extract` | `url_safety` |
| `todo` | 无 |
| `git_status` | 无 |

### 2.6 测试

- 每个 builtin 至少有 basic / error / options 三类测试
- 目录：`tests/tools/builtin/test_xxx.py`

---

## 3. Wave 2：自定义工具系统

### 3.1 目标

允许用户在不改 Python 代码的情况下注册自定义工具，范围限定为两种：

- `http_custom`
- `shell_custom`

不做：

- `python_custom`
- `script_custom`
- workflow engine 风格的条件分支 / 循环

### 3.2 配置契约

自定义工具配置字段必须统一，禁止一份文档里出现多套命名。

#### `http_custom` YAML 示例

```yaml
name: jira_search
type: http_custom
description: Search Jira tickets
source: custom
enabled: true
options:
  method: GET
  url_template: "https://jira.company.com/rest/api/2/search?jql=${jql}"
  headers:
    Authorization: "Bearer ${JIRA_TOKEN}"
  timeout_s: 10
  max_bytes: 102400
  allowed_domains:
    - jira.company.com
```

#### `shell_custom` YAML 示例

```yaml
name: deploy_staging
type: shell_custom
description: Deploy to staging
source: custom
enabled: true
options:
  command_template: "kubectl apply -f k8s/staging/ --namespace=${namespace}"
  workdir: "/app"
  timeout_s: 60
```

#### 类型说明

| type | 能力 | 安全约束 |
|---|---|---|
| `http_custom` | HTTP 请求 + 模板变量 | 只能访问 `allowed_domains` |
| `shell_custom` | 固定命令模板 + 模板变量 | 必须走与 `shell_exec` 同一套 shell 安全链路 |

### 3.3 数据模型

```python
@dataclass(frozen=True)
class ToolEntry:
    name: str
    type: str
    enabled: bool
    options: dict[str, Any]
    source: Literal["builtin", "custom"] = "builtin"
    description: str = ""
    custom_type: str | None = None
```

SQL migration：

```sql
ALTER TABLE tools ADD COLUMN source TEXT DEFAULT 'builtin';
ALTER TABLE tools ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE tools ADD COLUMN custom_type TEXT DEFAULT NULL;
```

### 3.4 `CustomTool` 基类

```python
class CustomTool(BaseTool, ABC):
    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        if entry.custom_type == "http_custom":
            return HttpCustomTool.create(entry)
        if entry.custom_type == "shell_custom":
            return ShellCustomTool.create(entry)
        raise ConfigError(...)
```

### 3.5 `http_custom` 设计

- 使用 `url_template`
- 可选 `body_template`
- `headers` 中也允许模板变量
- schema 从模板变量中自动提取 required fields
- 返回截断后的响应文本

### 3.6 `shell_custom` 设计

这是本设计里最重要的安全约束之一：

- `shell_custom` 不能直接 `asyncio.create_subprocess_shell(...)`
- 否则会绕过当前按 `tool_name == "shell_exec"` 命中的 guardrails
- 正确做法是二选一：
  1. 复用 `ShellExecTool` 的执行入口
  2. 把 shell guardrails 下沉到 shared shell runner / shared shell policy 层

实现要求：

- `shell_custom` 和 `shell_exec` 必须命中同一套 regex / approval / timeout / output truncation 规则
- `shell_custom` 的运行时安全等级不得低于 `shell_exec`

### 3.7 `ToolRegistry` 加载逻辑

```python
@classmethod
def build(cls, entry: ToolEntry) -> BaseTool:
    if entry.source == "custom":
        return CustomTool.create(entry)
    return cls._builders[entry.type](entry)
```

加载规则：

1. builtin 先注册
2. 再读取 enabled tools
3. `source=custom` 走 `CustomTool.create`

#### Name 冲突策略

这里不留开放问题，直接写死：

- custom 工具名不能与 builtin 重名
- custom 工具之间也不能同名
- 不存在“custom 覆盖 builtin”的语义
- 拒绝策略发生在 create/update 阶段，而不是启动时 silent override

### 3.8 Create / Update 校验

这是第二个关键约束：

自定义工具在写入 DB 前必须做 preflight probe。

流程：

1. 组装 `ToolEntry`
2. 调 `ToolRegistry.build(entry)`
3. 调 `tool.schema()`
4. 校验关键 options 形状
5. 只有 probe 全通过才允许入库或启用

原因：

- 避免“坏配置已入库，下一次 bootstrap 整体失败”
- 管理面应该让单工具报错，而不是让 agent 无法启动

### 3.9 启动时容错

即便有历史脏数据，bootstrap 也不能整体崩掉。

要求：

- enabled custom tool 构建失败时，记录 audit/log
- skip 掉该工具，继续启动 agent
- 在 CLI / sidecar / UI 上标记为 `invalid_tool_config`

### 3.10 测试

- `test_http_custom_tool_basic`
- `test_http_custom_tool_post_json`
- `test_shell_custom_tool_basic`
- `test_custom_tool_name_conflict`
- `test_custom_tool_invalid_template`
- `test_custom_tool_probe_before_persist`
- `test_bootstrap_skips_invalid_custom_tool`

---

## 4. Wave 3：CRUD CLI + Sidecar + UI

### 4.1 CLI

```bash
chariot tool list
chariot tool show <name>
chariot tool add --name my_api --type http_custom --from-file my_api.yaml
chariot tool edit <name> --from-file my_api_v2.yaml
chariot tool remove <name>
chariot tool enable <name>
chariot tool disable <name>
chariot tool config <name> -o timeout_s=30
```

CLI 约束：

- `add/edit` 在 commit 前必须先 probe
- builtin 不可删除
- custom 不可改名，不可改类型

### 4.2 Sidecar RPC

```json
{"method": "list_tools", "params": {}}
{"method": "show_tool", "params": {"name": "my_api"}}
{"method": "add_custom_tool", "params": {"name": "my_api", "custom_type": "http_custom", "options": {...}}}
{"method": "update_custom_tool", "params": {"name": "my_api", "options": {...}}}
{"method": "remove_custom_tool", "params": {"name": "my_api"}}
```

RPC 约束：

- `show_tool` 返回 `schema/options/source/custom_type/health`
- `add_custom_tool` / `update_custom_tool` 在写 DB 前必须 probe
- 失败返回 `invalid_tool_config`
- 统一使用 `remove_custom_tool` 命名，不再混用 `delete/remove`

### 4.3 桌面 `/tools`

- 列表视图：显示 builtin/custom badge
- 详情弹窗：显示 schema / options / health
- custom 工具允许编辑
- builtin 工具只读
- invalid 配置要显式高亮

### 4.4 测试

- `test_tool_list_shows_builtin_and_custom`
- `test_tool_add_custom_persists_to_db`
- `test_tool_remove_builtin_rejected`
- `test_tool_remove_custom_ok`
- `test_tool_edit_custom_updates_options`
- `test_tool_show_invalid_health`

---

## 5. 模块布局总览

```text
chariot/
├── tools/
│   ├── base.py
│   ├── registry.py
│   ├── custom.py
│   └── builtin/
├── models/
│   └── tool.py
├── database/
│   ├── models.py
│   └── migrations/
│       └── v23_tool_expansion.sql
├── repos/
│   └── tool_repo.py
├── cli/commands/
│   └── tool.py
└── sidecar/methods/
    └── tool.py
```

---

## 6. 安全设计

### 6.1 Custom Tool 沙箱

| 类型 | 约束 |
|---|---|
| `http_custom` | 只能访问 `options.allowed_domains`；默认超时 10s；默认响应上限 100KB |
| `shell_custom` | LLM 只能填模板变量；不得改变命令结构；必须经过与 `shell_exec` 同一套 guardrails / approval / timeout |

### 6.2 Builtin 不可删

```python
async def delete(self, name: str) -> None:
    entry = await self.get_entry(name)
    if entry.source == "builtin":
        raise ConfigError(...)
```

### 6.3 Name 唯一性

- builtin 名称全局唯一
- custom 名称不能与 builtin 重名
- 两个 custom 也不能同名
- 不支持 custom override builtin

### 6.4 入库前 Probe

所有 custom create / update 都必须先 probe，再 commit。

### 6.5 启动时 Fault Isolation

- 单个 invalid custom tool 不得拖垮 agent bootstrap
- 失败要可观测：log / audit / admin surface 都要能看到

---

## 7. 验收标准

### 7.1 Wave 1

- [ ] 7 个新增 builtin 工具全部可调用
- [ ] 每个工具至少 3 个单测
- [ ] `ruff / pyright / pytest` 全绿
- [ ] `todo` 在同一 session 内可持续追踪状态

### 7.2 Wave 2

- [ ] 可添加 `http_custom`
- [ ] 可添加 `shell_custom`
- [ ] `shell_custom` 不绕过 `shell_exec` guardrails
- [ ] custom 配置持久化到 DB
- [ ] custom 与 builtin 重名时创建被拒绝
- [ ] invalid custom config 不允许入库
- [ ] 历史坏配置不会导致 agent 无法启动

### 7.3 Wave 3

- [ ] CLI `add/edit/remove/show/list` 可用
- [ ] sidecar RPC 可用
- [ ] 桌面 `/tools` 显示 builtin/custom/invalid 状态
- [ ] demo doc 完成

---

## 8. 与 B8-analytics 的关系

本文档是 B8 第一版：扩展工具种类 + 管理能力。  
`docs/B8-tool-analytics-design.md` 是 B8 第二版：分析工具使用情况。

执行顺序：

1. 先做本文档
2. 再做 analytics / curator

原因：

- 先把工具能力和管理面补齐
- 再分析“哪些工具常用、哪些失败、哪些应下线”

