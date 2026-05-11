# Agent / Prompt / Tool / Provider 绑定演示

> 范围:0.7.2-tool 后五个底座(prompt / context / tool / provider / agent)在一次对话里怎么协同
> 目标:可直接复制粘贴的端到端 demo,展示 `agent_profile` 怎么把四个 binding(prompt_bundle / tool_profile / provider_profile + 未来的 context)收口

## 前置

```powershell
uv sync
Remove-Item .tmp\chariot-binding-demo.db -ErrorAction SilentlyContinue
```

环境变量(可选,真实 Claude):

```powershell
$env:ANTHROPIC_API_KEY = "sk-..."
```

不设 env 时所有命令依然能跑,走默认 seeded 的 `mock` provider(本地 echo)。

---

## 0. 五个底座一图

| 底座 | 管理对象 | 管理 CLI | agent_profile 字段 |
|---|---|---|---|
| **Prompt** | `prompt_bundles` + `prompt_versions` | `chariot prompt bundle {list,show,add,update,activate}` | `prompt_bundle` |
| **Context** | per-turn 装配(自动) | `chariot context {list,show}` 只读 | (无字段,自动 per turn)|
| **Tool** | `tools` + `toolsets` + `toolset_members` | `chariot tool {list,enable,disable}` + `chariot toolset {list,add,members add/remove}` | `tool_profile`(指向 toolset name)|
| **Provider** | `providers` + `provider_health` | `chariot provider {list,add,use,probe}` | `provider_profile`(指向 provider entry name)|
| **Agent** | `agent_profiles` | `chariot agent {list,add,update,remove}` | (本身)|

**Binding 链路**:`chariot chat --agent <name>` / task 执行入口 → `ChatRequest.agent_profile=<name>` → `AIAgent._resolve_binding` 解析 → 三个 binding 接到主流程:

```
profile.provider_profile  覆盖  req.provider_name
profile.prompt_bundle     覆盖  PromptRepo.get_active_bundle()
profile.tool_profile      过滤  self._tools(toolset 成员)
```

弱引用语义:任何 binding name dangling → 走 fallback,**不阻断 task**。

---

## 1. 配 provider

```powershell
uv run chariot provider list                                     # 看现有
uv run chariot provider add --name claude --type anthropic `
  --options '{"api_key_env":"ANTHROPIC_API_KEY","model":"claude-sonnet-4-6"}'
uv run chariot provider probe claude                             # 探活
uv run chariot provider use claude                               # 设为默认
```

---

## 2. 配 prompt bundle

```powershell
uv run chariot prompt bundle list
uv run chariot prompt bundle add --name research --layers `
  '[{"name":"base_system","source":"researcher prompt","content":"You are a careful researcher. Always cite sources."}]'
uv run chariot prompt bundle show research
# 注意:add 命令会把新 bundle 设为 active;如不希望 research 是全局 active,
# 跑完后再 activate 回 default:
uv run chariot prompt bundle activate default
```

---

## 3. 开工具 + 命名 toolset

```powershell
uv run chariot tool list                                         # 4 seeded fixtures
uv run chariot tool enable read_file
uv run chariot tool enable list_dir
uv run chariot toolset add --name fs_safe --description "只读文件操作" `
  --members read_file,list_dir
uv run chariot toolset show fs_safe
```

---

## 4. 配 agent_profile 绑三件套

```powershell
uv run chariot agent add --name researcher --role research `
  --provider-profile claude `
  --prompt-bundle research `
  --tool-profile fs_safe
uv run chariot agent show researcher
```

---

## 5. 用 agent_profile 直接 chat(0.7.2-tool 后支持)

```powershell
uv run chariot chat --agent researcher "看看 docs/ 目录,简单总结一下"
```

此时:
- provider 用 `claude`(由 `agent.provider_profile` 覆盖默认)
- system prompt 用 `research` bundle(由 `agent.prompt_bundle` 选定)
- 工具只有 `read_file` 和 `list_dir`(`fs_safe` toolset filter)

**stateful 多轮**:

```powershell
uv run chariot chat --agent researcher --conversation new "先看根目录"
# 输出会打印 new conversation ULID,记下来
uv run chariot chat --agent researcher --conversation <ULID> "现在挑 docs/ 看"
```

---

## 6. 通过 task 跑(B1 trace 自动记录)

```powershell
uv run chariot task add --goal "总结仓库 docs/ 的结构" --agent researcher
uv run chariot task list
uv run chariot task start <task-id>
```

跟 chat 直接调用等价,但走 task 路径会留下完整 trace。

---

## 7. trace 反查(B1 phase 4 起)

```powershell
uv run chariot trace list                                        # 列最近 turn
uv run chariot trace list --agent-profile researcher             # (B1 当前用 task / conversation 过滤)
uv run chariot trace list --task <task-id>
uv run chariot trace view <turn-id>                              # 树形展开 provider + tool + checkpoint
```

---

## 8. flag 冲突 / 优先级速查

| flag 组合 | 行为 |
|---|---|
| `--agent` + `--provider X` | `agent.provider_profile` 非空 → 覆盖 `--provider`(后者是 fallback) |
| `--agent` + `--model Y` | 不冲突;`--model` per-call 覆盖 entry.options.model |
| `--agent` + `--base-url` / `--api-key` | ⚠️ 这两个 keyed 到 `--provider` entry;若 agent 把路由切到别的 entry,patch 不命中 |
| `--agent` 全空 | 走全局 active bundle + 全量 enabled tools(0.7.0 行为)|

---

## 9. dangling reference 容错

- `agent.provider_profile = "ghost"`(不存在)→ `AIAgent` 解析时 profile 拿不到 → fallback 用 `req.provider_name`
- `agent.prompt_bundle = "ghost"` → `PromptRepo.get_bundle(...)` 返 None → fallback 用 active bundle
- `agent.tool_profile = "ghost_toolset"`(toolset 不存在)→ fallback 全量 enabled tools
- `agent.tool_profile = "empty_toolset"`(toolset 存在但成员空)→ `req.tools=[]`,等于关闭工具调用

所有这些情况 task 都**不会失败**,只是行为退化到 fallback。

---

## 10. 桌面 UI 等价操作

| CLI | UI 等价 |
|---|---|
| `chariot provider list` | Providers 页 |
| `chariot prompt bundle list` | Prompt 页 |
| `chariot toolset list` | Toolsets 页 |
| `chariot agent add ...` | Agents 页 "+ Add agent"(三个 binding 字段用 `<datalist>` 自动补全 toolset / bundle / provider 已有 name) |
| `chariot trace list / view` | Trace 页(B1 phase 6,未落)|
