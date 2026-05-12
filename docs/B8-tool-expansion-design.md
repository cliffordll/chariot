# B8 — 工具扩展与管理（Tool Expansion & Management）

> Milestone:`feat/0.8.7-toolmngn`
> 范围:扩充内置工具种类 + 支持自定义工具 + 完整 CRUD 管理
> 设计前提:
> - 现有 `tools` 表(v4)已有 `name/type/enabled/options`,缺 `source/description`
> - `ToolRegistry` 是代码级注册,`ToolEntry` 是配置形态
> - B5 guardrails 已接入 `tool_call_pre/post`
> - 模块归属:`chariot/tools/` 子包内新增 builtin + custom 支持

---

## 0. 为什么现在做

当前 chariot 只有 **5 个内置工具**:

| 工具 | 能力 | 状态 |
|---|---|---|
| read_file | 读文件 | ✅ 已有 |
| list_dir | 列目录 | ✅ 已有 |
| shell_exec | 执行命令 | ✅ 已有 |
| http_get | HTTP GET | ✅ 已有 |
| propose_skill | 提议 skill | ✅ B6 新增 |

**核心缺口**:
1. **有读无写** — agent 能读文件但不能写,只能走 shell_exec `echo "..." > file`,既不优雅也不安全
2. **无文件搜索** — agent 找不到文件时只能 `find` / `grep` 走 shell,丢失结构化输出
3. **无网页搜索** — http_get 只能访问已知 URL,不能主动搜索信息
4. **无任务管理** — 复杂任务没有 todo list 跟踪,agent 容易遗忘子目标
5. **无 git 集成** — 代码相关任务缺 git status/diff,agent 不知道仓库状态
6. **无法扩展** — 用户想加自己的 API 或命令,只能改代码再部署

**不做**:
- 动态代码生成(codegen)—— 安全风险高,留远期
- 完全开放的 script execution —— 同 codegen 风险
- Web crawl / spider —— 重,且 agent 用 search + extract 就够

---

## 1. Wave 拆分

| Wave | 内容 | 交付 | 阻塞 |
|---|---|---|---|
| **1** | 内置工具扩充 — 从 phalanx 移植 6 个工具:`write_file`, `edit_file`, `search_files`, `web_search`, `todo`, `git_status`;每个都走 `BaseTool` + `ToolRegistry` + guardrails;tests | 11 个内置工具可用 | wave 2 |
| **2** | 自定义工具系统 — `CustomTool` 基类 + `HttpCustomTool` / `ShellCustomTool` 两种类型;YAML 定义存 DB;`ToolRegistry` 加载时 union builtin + custom;tests | 用户可注册自定义工具 | wave 3 |
| **3** | 工具 CRUD CLI + sidecar — `chariot tool {add, edit, remove, list, show, enable, disable, config}` 完整命令集;sidecar 同步 RPC;桌面 `/tools` 页(列表 + 自定义工具表单);tests + demo doc | 工具可完整管理 | — |

> 3 wave 总量可控,wave 1 纯工具实现(工作量最大但路径清晰),wave 2-3 是管理能力。

---

## 2. Wave 1: 内置工具扩充

### 2.1 移植清单

从 phalanx 筛选 6 个最实用、与 chariot 架构兼容的工具:

| 工具名 | 来源 | 核心能力 | 对应 phalanx |
|---|---|---|---|
| `write_file` | 新增 | 写文件(覆盖/追加),支持目录自动创建 | `write_file_tool` |
| `edit_file` | 新增 | 基于 old_string→new_string 的文本替换(patch 的简化版) | `patch_tool` |
| `search_files` | 新增 | 文件内容搜索(grep),支持目录递归、正则、扩展名过滤 | `search_tool` |
| `web_search` | 新增 | 网页搜索,支持 Tavily / DuckDuckGo 后端 | `web_search_tool` |
| `web_extract` | 新增 | 网页内容提取(URL → markdown),支持 allowed_domains | `web_extract_tool` |
| `todo` | 新增 | session 内任务列表(CRUD),注入 conversation context | `todo_tool` |
| `git_status` | 新增 | 显示 git 仓库状态(分支、改动文件、未跟踪文件) | (phalanx 无独立工具,但从 terminal 分离) |

保留现有 5 个工具不变。wave 1 后总计 **12 个内置工具**。

### 2.2 各工具设计要点

#### write_file
```python
class WriteFileTool(BaseTool):
    """写文件工具。支持覆盖/追加,目录不存在自动创建。

    options:
        - default_mode: "overwrite" | "append" (默认 overwrite)
        - allow_outside_cwd: bool (默认 False;True 时 guardrail 失效)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path = input["path"]
        content = input["content"]
        mode = input.get("mode", self.default_mode)
        # guardrails: 同 B5 file_write_outside_cwd / file_write_secrets
```

#### edit_file
```python
class EditFileTool(BaseTool):
    """编辑文件工具。在文件中查找 old_string 替换为 new_string。

    设计理由:
    - LLM 生成整文件重写不现实(上下文太长)
    - diff/patch 对 LLM 来说格式太难
    - old_string→new_string 是 LLM 最自然的编辑方式(Phalanx 验证)

    options:
        - max_occurrences: int (默认 1;>1 时替换前 N 处)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path = input["path"]
        old_string = input["old_string"]
        new_string = input["new_string"]
        # 未找到 → is_error=True, 提示 snippet
        # 找到多处且未指定 occurrence → is_error=True, 提示用 count 参数
```

#### search_files
```python
class SearchFilesTool(BaseTool):
    """文件内容搜索工具。grep + find 的合体。

    options:
        - max_results: int (默认 50)
        - max_file_size_mb: float (跳过超大文件,默认 10)

    input:
        - pattern: str (搜索文本或正则)
        - path: str (搜索根目录,默认 ".")
        - file_extension: str | None (如 ".py", 默认 None = 全部)
        - use_regex: bool (默认 False)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 递归遍历 path 下所有匹配 file_extension 的文件
        # 逐行匹配 pattern,返 {file, line, content} 列表
        # 受 guardrails: file_write_outside_cwd 的读版本约束
```

#### web_search
```python
class WebSearchTool(BaseTool):
    """网页搜索工具。

    后端支持(优先级):
    1. Tavily (需要 TAVILY_API_KEY)
    2. DuckDuckGo (免费,无 API key,通过 duckduckgo-search 库)
    3. 降级:返回错误提示用户配置 API key

    options:
        - backend: "tavily" | "duckduckgo" (默认 duckduckgo)
        - max_results: int (默认 5)
        - include_answer: bool (Tavily 支持直接返摘要,默认 True)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        query = input["query"]
        # 调用对应后端,返 [{title, url, snippet}] 列表
```

#### web_extract
```python
class WebExtractTool(BaseTool):
    """网页内容提取。URL → 可读 markdown。

    实现:
    - 先用 httpx GET 拉取 HTML
    - 用 readability-lxml 或 trafilatura 提取正文
    - fallback: 返原始 HTML 的前 N 字符

    options:
        - max_bytes: int (默认 100KB)
        - allowed_domains: list[str] | None (None = 不限)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        url = input["url"]
        # domain 检查 → 拉取 → 提取正文 → 截断 → 返 markdown
```

#### todo
```python
class TodoTool(BaseTool):
    """Session 内任务列表管理。

    设计:状态挂在 AIAgent 实例上(非持久化),session 结束即清空。
    每次调用返完整列表,agent 通过 system prompt 或上下文看到当前任务。

    input:
        - action: "add" | "update" | "remove" | "list" | "clear"
        - id: str (add/update/remove 用,agent 自分配)
        - text: str (add/update 用)
        - status: "pending" | "in_progress" | "completed" | "cancelled" (update 用)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 操作 agent._todo_store (TodoStore dict)
        # 返格式化后的完整 todo 列表
```

> TodoStore 挂在 AIAgent 上,不是 DB 持久化。因为 todo 是"当前 session 的临时计划",跨 session 没意义。B3 memory 已负责长期记忆。

#### git_status
```python
class GitStatusTool(BaseTool):
    """显示 git 仓库状态。

    不执行修改操作(只读),比 shell_exec `git status` 安全且输出结构化。

    input:
        - path: str (仓库路径,默认 ".")
        - include_diff: bool (是否包含 diff 摘要,默认 False)
    """

    def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 用 subprocess 跑 git status --short / git diff --stat
        # 结构化输出:branch, ahead/behind, modified[], untracked[], diff_summary?
```

### 2.3 模块布局

```
chariot/tools/builtin/
├── __init__.py           # EXTEND: register 新增 7 个工具
├── read_file.py          # 已有
├── list_dir.py           # 已有
├── shell_exec.py         # 已有
├── http_get.py           # 已有
├── propose_skill.py      # 已有(B6)
├── write_file.py         # NEW
├── edit_file.py          # NEW
├── search_files.py       # NEW
├── web_search.py         # NEW
├── web_extract.py        # NEW
├── todo.py               # NEW
└── git_status.py         # NEW
```

### 2.4 依赖

新增 Python 依赖(加进 `pyproject.toml`):

```toml
dependencies = [
    # ... existing
    "trafilatura>=1.6",      # web_extract HTML → text
    "duckduckgo-search>=5.0", # web_search 免费后端
]
```

Tavily 走 httpx 直接调用 REST API,不引 SDK。

### 2.5 Guardrails 接入

每个新工具在 `tool_call_pre` 时走 `GuardrailEngine`:

| 工具 | 命中规则 | 说明 |
|---|---|---|
| write_file | `file_write_outside_cwd` | 默认 DENY 写 cwd 外 |
| write_file | `file_write_secrets` | 写内容含 api_key 等敏感信息 |
| edit_file | `file_write_outside_cwd` | 同 write_file |
| search_files | 无新增规则 | 只读,风险低 |
| web_search | 无新增规则 | 只读,风险低 |
| web_extract | `url_safety` | 提取恶意 URL 内容(复用 B5 URL 规则) |
| todo | 无 | 纯内存操作 |
| git_status | 无 | 只读 |

### 2.6 测试

每个新工具至少 3 个测试:
- `test_xxx_basic` —— 正常路径
- `test_xxx_error` —— 错误路径(文件不存在/URL 无效/权限拒绝)
- `test_xxx_options` —— options 配置生效

放在 `tests/tools/builtin/test_xxx.py`。

---

## 3. Wave 2: 自定义工具系统

### 3.1 问题

用户想加自己的 API 或命令,现在只能:
1. Fork 代码 → 写新 Python 类 → 重新部署
2. 走 shell_exec 硬编码命令

两种方式都不优雅。需要一个"不用写代码就能注册工具"的机制。

### 3.2 设计:两种自定义工具类型

```yaml
# 自定义工具 YAML 定义示例(存在 DB tools 表)
name: jira_search
type: http_custom          # 工具运行时类型
description: Search Jira tickets
source: custom             # 区分 builtin / custom
enabled: true
options:
  method: GET
  url: https://jira.company.com/rest/api/2/search
  headers:
    Authorization: Bearer ${JIRA_TOKEN}
  query_params:
    jql: "{jql}"
  timeout_s: 10
```

```yaml
name: deploy_staging
type: shell_custom
description: Deploy to staging
source: custom
enabled: true
options:
  command: "kubectl apply -f k8s/staging/ --namespace={namespace}"
  workdir: "/app"
  timeout_s: 60
```

**类型说明**:

| type | 能力 | 安全 |
|---|---|---|
| `http_custom` | 发送 HTTP 请求(GET/POST/PUT/DELETE),支持 template 变量 | 走 allowed_domains + 无文件系统访问 |
| `shell_custom` | 执行预定义命令模板,变量由 LLM 填充 | 走 shell_exec guardrails(危险命令 regex) |

**不做**:
- `python_custom` / `script_custom` —— 执行任意代码,安全风险不可控
- 复杂条件分支/循环 —— 不是 workflow engine

### 3.3 数据模型扩展

**`ToolEntry` (chariot/models/tool.py)**:
```python
@dataclass(frozen=True)
class ToolEntry:
    name: str
    type: str
    enabled: bool
    options: dict[str, Any]
    source: Literal["builtin", "custom"] = "builtin"   # NEW
    description: str = ""                                 # NEW
    custom_type: str | None = None                        # NEW;http_custom / shell_custom
```

**`ToolRow` (database/models.py)**:
```sql
-- migration v23_tool_expansion.sql
ALTER TABLE tools ADD COLUMN source TEXT DEFAULT 'builtin';
ALTER TABLE tools ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE tools ADD COLUMN custom_type TEXT DEFAULT NULL;

-- custom 工具存 YAML 序列化后的 options(保持现有 options 列)
```

### 3.4 `CustomTool` 基类 + 子类

```python
# chariot/tools/custom.py

class CustomTool(BaseTool, ABC):
    """自定义工具基类。从 ToolEntry 的 options 中读取配置,动态执行。"""

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        if entry.custom_type == "http_custom":
            return HttpCustomTool.create(entry)
        if entry.custom_type == "shell_custom":
            return ShellCustomTool.create(entry)
        raise ConfigError(f"未知 custom_type: {entry.custom_type!r}")


class HttpCustomTool(CustomTool):
    """HTTP 自定义工具。"""

    def __init__(self, name: str, method: str, url_template: str, ...) -> None: ...

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 1. 用 input 填充 url_template / body_template
        # 2. httpx 发送请求
        # 3. 返 {status, body}(截断到 max_bytes)


class ShellCustomTool(CustomTool):
    """Shell 自定义工具。"""

    def __init__(self, name: str, command_template: str, workdir: str, ...) -> None: ...

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 1. 用 input 填充 command_template
        # 2. asyncio.create_subprocess_shell 执行
        # 3. 返 {stdout, stderr, returncode}
```

### 3.5 `ToolRegistry` 加载逻辑

```python
# chariot/tools/registry.py —— build 方法扩展

@classmethod
def build(cls, entry: ToolEntry) -> BaseTool:
    if entry.source == "custom":
        return CustomTool.create(entry)
    # 原有 builtin 路径
    builder = cls._builders[entry.type]
    return builder(entry)
```

**启动时加载**:
1. `AIAgent.bootstrap` → 先 register 所有 builtin 工具(代码里 import)
2. 再读 `ToolRepo.list_entries(source="custom")` → 对每个 custom entry 用 `CustomTool.create`
3. custom 工具 name 不能跟 builtin 重名 → 冲突时 custom 覆盖或报错

### 3.6 测试

- `test_http_custom_tool_basic` —— GET 请求模板填充
- `test_http_custom_tool_post_json` —— POST + body template
- `test_shell_custom_tool_basic` —— 命令模板填充
- `test_custom_tool_name_conflict` —— custom 与 builtin 重名处理
- `test_custom_tool_invalid_template` —— 模板变量缺失报错

---

## 4. Wave 3: CRUD CLI + Sidecar

### 4.1 CLI 命令集

```bash
# 列出所有工具(builtin + custom)
chariot tool list
# 输出: name | type | source | enabled | description

# 查看单个工具详情
chariot tool show <name>
# 输出: schema + options + source + 最近调用统计(从 audit)

# 添加自定义工具
chariot tool add --name my_api --type http_custom --from-file my_api.yaml
chariot tool add --name deploy --type shell_custom --interactive

# 编辑自定义工具
chariot tool edit <name> --from-file my_api_v2.yaml

# 删除自定义工具(builtin 不可删)
chariot tool remove <name>
# 确认: "remove custom tool 'my_api'? [y/N]"

# 启用/禁用(已有,扩展支持 custom)
chariot tool enable <name>
chariot tool disable <name>

# 修改配置(已有,扩展支持 custom)
chariot tool config <name> -o timeout_s=30
```

### 4.2 Sidecar RPC

```json
// list_tools → [{name, type, source, enabled, description}]
{"method": "list_tools", "params": {}}

// get_tool_schema → {name, schema, options, source}
{"method": "get_tool_schema", "params": {"name": "my_api"}}

// add_custom_tool
{"method": "add_custom_tool", "params": {"name": "my_api", "custom_type": "http_custom", "options": {...}}}

// remove_custom_tool
{"method": "remove_custom_tool", "params": {"name": "my_api"}}

// update_custom_tool
{"method": "update_custom_tool", "params": {"name": "my_api", "options": {...}}}
```

### 4.3 桌面 UI

在桌面端 `/tools` 页:
- **列表视图**:所有工具卡片,显示 source badge(builtin/custom)
- **详情弹窗**:schema + options JSON 编辑器(只读 for builtin,可编辑 for custom)
- **添加按钮**:表单选择 http_custom / shell_custom → 动态表单 → 保存到 DB
- **删除按钮**:custom 工具显示删除图标,builtin 隐藏

### 4.4 测试

- `test_tool_list_shows_builtin_and_custom`
- `test_tool_add_custom_persists_to_db`
- `test_tool_remove_builtin_rejected`
- `test_tool_remove_custom_ok`
- `test_tool_edit_custom_updates_options`

---

## 5. 模块布局总览

```
chariot/
├── tools/
│   ├── __init__.py           # EXTEND: register builtin + custom
│   ├── base.py               # 已有(BaseTool)
│   ├── registry.py           # EXTEND: + custom tool loading
│   ├── custom.py             # NEW: CustomTool / HttpCustomTool / ShellCustomTool
│   └── builtin/
│       ├── __init__.py       # EXTEND: + 7 个新工具 register
│       ├── read_file.py      # 已有
│       ├── list_dir.py       # 已有
│       ├── shell_exec.py     # 已有
│       ├── http_get.py       # 已有
│       ├── propose_skill.py  # 已有
│       ├── write_file.py     # NEW
│       ├── edit_file.py      # NEW
│       ├── search_files.py   # NEW
│       ├── web_search.py     # NEW
│       ├── web_extract.py    # NEW
│       ├── todo.py           # NEW
│       └── git_status.py     # NEW
│
├── models/
│   └── tool.py               # EXTEND: + source / description / custom_type
│
├── database/
│   └── models.py             # EXTEND: ToolRow +3 列
│   └── migrations/
│       └── v23_tool_expansion.sql  # NEW
│
├── repos/
│   └── tool_repo.py          # EXTEND: + create / delete / update_full
│
├── cli/commands/
│   └── tool.py               # EXTEND: + add / edit / remove / 重构 list/show
│
└── sidecar/methods/
    └── __init__.py           # EXTEND: + tool CRUD RPC

tests/
├── tools/builtin/            # 已有
│   ├── test_write_file.py    # NEW
│   ├── test_edit_file.py     # NEW
│   ├── test_search_files.py  # NEW
│   ├── test_web_search.py    # NEW
│   ├── test_web_extract.py   # NEW
│   ├── test_todo.py          # NEW
│   └── test_git_status.py    # NEW
├── tools/
│   └── test_custom_tool.py   # NEW
└── platform/
    └── test_tool_crud.py     # NEW
```

---

## 6. 安全设计

### 6.1 Custom Tool 沙箱

| 类型 | 约束 |
|---|---|
| `http_custom` | 只能访问 `options.allowed_domains` 内的域名;默认 timeout 10s;最大响应 100KB |
| `shell_custom` | 命令模板预定义,LLM **只能填变量**,不能改命令结构;走 shell_exec guardrails |

### 6.2 Builtin 不可删

```python
# ToolRepo.delete
async def delete(self, name: str) -> None:
    entry = await self.get_entry(name)
    if entry.source == "builtin":
        raise ConfigError(f"builtin 工具不可删除: {name!r}")
    # 继续删除
```

### 6.3 Name 唯一性

- builtin 工具名全局唯一（代码级保证）
- custom 工具名不能跟 builtin 重名（register 时检查）
- 两个 custom 工具不能同名（DB unique constraint）

---

## 7. 验收标准

### 7.1 wave 1
- [ ] 7 个新内置工具全部可注册、可调用、有测试
- [ ] 每个新工具 ≥3 个单元测试（正常/错误/options）
- [ ] ruff / pyright / pytest 全绿
- [ ] CLI `chariot tool list` 显示 12 个工具

### 7.2 wave 2
- [ ] 可添加 `http_custom` 工具并正常调用
- [ ] 可添加 `shell_custom` 工具并正常调用
- [ ] custom 工具配置持久化到 DB
- [ ] custom 与 builtin 重名时拒绝注册

### 7.3 wave 3
- [ ] CLI `add/edit/remove` 完整可用
- [ ] Sidecar 4 个 RPC 可用
- [ ] Desktop `/tools` 页显示 custom 工具
- [ ] ruff / pyright / pytest 全绿
- [ ] demo doc 完成

---

## 8. 与 B8-analytics 的关系

本文档是 **B8 第一版**（工具扩展 + 管理），`docs/B8-tool-analytics-design.md` 是 **B8 第二版**（工具分析/curator）。

执行顺序:
1. 先做本文档（扩展工具种类 + 管理能力）—— agent 有更多工具可用,管理更方便
2. 后做 analytics（分析工具使用情况）—— 有数据后才值得分析

两个版本共享同一 milestone 分支 `feat/0.8.7-toolmngn`，analytics 在 wave 3 完成后追加。
