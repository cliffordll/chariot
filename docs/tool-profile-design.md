# Tool Profile / Toolset 设计草案

> 状态:草案,未实现,等用户拍板细节后再落地
> 范围:`agent_profile.tool_profile` 字段活化 + 命名 toolset 实体首次落地
> 不在范围:tool audit / tool policy(`DEVELOPMENT-tool.md` 的另两块)、approval workflow、generated tool lifecycle

## 决策背景

`agent_profile.tool_profile` 在 A6 milestone 当成 dead string 留下;A6-profile-wiring.md(已归档 docs/history/0.7.2/)曾讨论 A/B/C 三方案,未决断。`DEVELOPMENT-tool.md`(草案)又独立设计了一个 `toolset` 概念,本质跟 tool_profile 是同一件事。

本文采用 **D 方案 = A + toolset 合并** —— 一次性引入命名 toolset 实体,同时活化 tool_profile 接线,不再让两个名字分两套实现。

## 设计意图

把 `tool_profile` / `toolset` 收口成一个东西:**命名一组工具的引用 + agent 可绑定**。

- toolset 是命名实体:`name / description / members(tool name list)`,跟 `provider entry` / `prompt bundle` / `agent profile` 同款"四件套"(repo / domain service / sidecar Api / CLI + UI)
- agent_profile 通过 `tool_profile` 列引用 toolset name(列名保持不变,语义升级)
- task 执行时,AIAgent 按 agent_profile.tool_profile 解析出 toolset 成员,作为 `req.tools` 的来源(覆盖默认全量行为)

### toolset 的两种语义:不混用

| 语义 | 用途 | 是否动 `tool.enabled` |
|---|---|---|
| **filter**(本草案采用) | per-agent 工具集合过滤;runtime 选用 | ❌ 不动 |
| apply / macro | admin 一键批量启用 / 禁用 tools 表 | ✅ 动(但只在 admin 显式执行时) |

为什么 filter 优先:
- 多 agent 并发时,每个 task 拿自己的 toolset 过滤,互不影响
- `tool.enabled` 仍然是全局 source of truth,toolset 不在 runtime 改它
- toolset 作为 admin 宏(`toolset apply <name>`)可以单独保留,但**本草案不在本阶段做**,留 `DEVELOPMENT-tool.md` 阶段再考虑

## 目标结构

### Schema

新 migration `v15_toolsets.sql`:

```sql
CREATE TABLE toolsets (
    name TEXT PRIMARY KEY,
    description TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE toolset_members (
    toolset_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (toolset_name, tool_name),
    FOREIGN KEY (toolset_name) REFERENCES toolsets(name) ON DELETE CASCADE
);
```

- 不动 `agent_profiles.tool_profile` 列名(避免数据迁移成本;列含义升级为 "toolset name 的引用,允许 dangling reference")
- 不动 `tools` 表
- agent_profile → toolset 关系**不加 FK**(允许引用未来才会创建的 toolset name,跟 provider_profile / prompt_bundle 现在的弱引用风格一致)

### 代码组织(对齐 0.7.2 已确立模式)

```
chariot/models/toolset.py        # ToolsetEntry / ToolsetMember dataclass
chariot/services/toolset.py      # ToolsetService:list/get/create/update/delete/members/...
chariot/repos/toolset_repo.py    # ToolsetRepo:纯数据访问
chariot/sidecar/services/toolset.py  # ToolsetApi:RPC 适配
chariot/sidecar/methods/toolset.py   # ToolsetMethods:RPC 入口
chariot/cli/commands/toolset.py      # chariot toolset {list, show, add, update, remove, members add/remove}
```

### 运行时接线

`AIAgent._inject_default_tools` 加分支:

```python
def _inject_default_tools(self, req: ChatRequest, agent_profile: AgentProfile | None) -> ChatRequest:
    if req.tools is not None:
        return req  # 调用方显式指定,透传
    if not self._tools:
        return req
    enabled_tools = self._tools  # 当前 AIAgent 持有的 enabled tools
    if agent_profile is not None and agent_profile.tool_profile is not None:
        members = await ...  # 解析 toolset 成员
        enabled_tools = {name: tool for name, tool in enabled_tools.items() if name in members}
    schemas = [self._tool_schema(tool) for tool in enabled_tools.values()]
    return dataclasses.replace(req, tools=schemas)
```

`agent_profile` 来源:
- task 执行入口(worker / start-run / handler)在构造 ChatRequest 前读 task.agent_profile → `AgentRepo.get(name)`,把整个 `AgentProfile` 透传给 `_inject_default_tools`
- stateless chat / 没有 agent_profile 的路径:`agent_profile=None`,走原全量 fallback

### 错误语义

- toolset 引用 dangling(agent_profile.tool_profile 指向不存在的 toolset)→ 走 fallback(挂全量 enabled),log warning,不阻断 task
- toolset 内含 tool name 在当前 `tools` 表里不存在 → 静默跳过该成员,只挂剩下能解析的,log warning
- toolset 为空(成员 list = [])→ `req.tools = []`,等同关闭工具调用

理由:agent_profile.tool_profile 是"软引用",task 不应因为引用错误失败。

## 执行顺序

跟前面 9 步重构同款,按 commit 拆分:

1. **第一步:DB 层 + domain 层**
   - migration `v15_toolsets.sql`
   - `chariot/models/toolset.py`、`chariot/repos/toolset_repo.py`、`chariot/services/toolset.py`
   - 单测:repo + service
2. **第二步:sidecar 层**
   - `chariot/sidecar/services/toolset.py:ToolsetApi`
   - `chariot/sidecar/methods/toolset.py` + 注册到 `sidecar/methods/__init__.py`
   - `chariot/sidecar/services/__init__.py` re-export
   - 单测:sidecar method dispatch
3. **第三步:CLI**
   - `chariot/cli/commands/toolset.py`:`list / show / add / update / remove`、`members add / remove`
   - 注册到 `chariot/cli/__main__.py`
4. **第四步:AIAgent 接线**
   - `_inject_default_tools` 加 `agent_profile` 参数 + toolset 解析
   - 调用方更新:`agent/run.py` task 执行路径透传 agent_profile
   - 单测:agent_profile.tool_profile = 已存在 toolset → filter 生效;dangling → fallback;empty toolset → 关工具
5. **第五步:桌面 UI**
   - 新 Toolsets 页:list / show / edit / delete + members 编辑
   - Agents 页 `tool_profile` 输入框升级为 `<datalist>` 列已知 toolset name
6. **第六步:文档收尾**
   - `docs/DEVELOPMENT.md` 加一条 "tool_profile 已接线"
   - 本草案归档到 `docs/history/<version>/`

## 验收标准

- `toolsets` + `toolset_members` 两表存在,migration v15 跑通
- `chariot toolset list / show / add / remove / members add / remove` 全部 CLI 命令可用
- 桌面 UI 有 Toolsets 页,能 CRUD;Agents 页 tool_profile 字段升级 datalist
- 给一个 agent_profile 绑 toolset,创建 task 跑 chat,确认 `req.tools` 只含 toolset 成员
- agent_profile.tool_profile = NULL → 走全量 fallback(行为同重构前)
- agent_profile.tool_profile 指向不存在的 toolset → fallback + warning,task 不失败
- ruff / pyright / pytest 全套通过

## 验收方法

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright chariot/
uv run pytest -q
```

烟测:

```powershell
uv run chariot toolset add --name fs_safe --description "只读文件操作"
uv run chariot toolset members add fs_safe read_file
uv run chariot toolset members add fs_safe list_dir
uv run chariot agent update <name> --tool-profile fs_safe
uv run chariot task create --goal "看一下这个仓库根目录" --agent-profile <name>
uv run chariot task start <task-id>
# 确认 task 跑出来只用了 read_file / list_dir
```

桌面 UI:Toolsets 页能管理 toolset;Agents 页 tool_profile 选择器列出已有 toolset。

## 当前约束 / 风险

- **R1.** Toolset 命名跟 DEVELOPMENT-tool.md 草案对齐(都叫 toolset),不另起 `tool_profiles` 表名;`agent_profiles.tool_profile` 列名暂保(避免数据迁移),语义上视为 toolset name 引用
- **R2.** Toolset 只做 filter 语义,不做 apply 语义。`tool.enabled` 在 runtime 保持不变。若以后开 `chariot toolset apply <name>` 这种 admin 宏,留给 `DEVELOPMENT-tool.md` 阶段
- **R3.** Dangling reference(agent_profile.tool_profile 指向不存在 toolset)走 fallback 不失败 —— 跟 provider_profile / prompt_bundle 的弱引用风格一致;不加 FK 约束
- **R4.** AIAgent 内部 lifecycle 是 lazy load,一个 AIAgent 实例的 `self._tools` 是 startup 时装载的全量 enabled tools;toolset filter 发生在 `_inject_default_tools`,不需要重启 AIAgent
- **R5.** 改动跨 6 个 surface(DB / domain / sidecar / CLI / runtime / UI),建议作为独立 milestone 推进,不要混进其它功能开发的 commit 链
