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

### 验证

```powershell
uv run pytest tests/providers/ tests/sidecar/test_provider_methods_extra.py tests/cli/test_provider_parse.py tests/cli/test_provider_status_help.py -q
```

---

## 2. 配 prompt bundle

```powershell
uv run chariot prompt list
uv run chariot prompt add research --layer `
  '[{"name":"base_system","source":"researcher prompt","content":"You are a careful researcher. Always cite sources."}]'
uv run chariot prompt show research
# 注意:add 命令会把新 bundle 设为 active;如不希望 research 是全局 active,
# 跑完后再 activate 回 default:
uv run chariot prompt activate default
```

### 验证

```powershell
uv run pytest tests/platform/test_prompt_management.py tests/agent/test_prompt_system.py tests/cli/test_prompt_commands.py -q
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
uv run chariot toolset list
```

### 验证

```powershell
uv run pytest tests/platform/test_toolset_foundations.py tests/sidecar/test_tool_methods.py tests/sidecar/test_toolset_methods.py tests/cli/test_tool_commands.py tests/cli/test_toolset_commands.py tests/agent/test_tool_execution.py -q
```

---

## 4. 配 agent_profile 绑三件套

```powershell
uv run chariot agent add --name researcher --role research --provider-profile ollama-qwen --prompt-bundle research --tool-profile fs_safe
uv run chariot agent show researcher
```

### 验证

```powershell
uv run pytest tests/agent/test_agent_profile_binding.py -q
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

**REPL 交互模式**(不传 message text 自动进入):

```powershell
uv run chariot chat --agent researcher
# 进入 REPL,提示符 ›
›  先看根目录有哪些文件
›  挑 docs/ 看
›  /conversation new     # 切到新会话(stateful)
›  /conversation off     # 切回 stateless
›  /provider             # 看本轮实际 provider(已被 agent.provider_profile 覆盖)
›  /tool                 # 看本轮可用工具(已被 agent.tool_profile 过滤)
›  /help                 # 列全部 slash 命令
›  /exit                 # 退出
```

REPL 内 agent binding 一次性绑定到本次 session,中途切 `/provider` 只覆盖本次会话的
provider,不动 agent_profile 解析;退出后下次重新进入会重新读 agent。

### 验证

```powershell
uv run pytest tests/agent/test_run.py tests/agent/test_loop.py tests/agent/test_chat_request.py tests/agent/test_chat_event.py tests/sidecar/test_chat_method.py tests/cli/test_repl_slash.py tests/cli/test_repl_tool_render.py -q
```

---

## 6. 通过 task 跑(B1 trace 自动记录)

```powershell
uv run chariot task create --goal "总结仓库 docs/ 的结构" --agent researcher1
uv run chariot task list
uv run chariot task start <task-id>
```

跟 chat 直接调用等价,但走 task 路径会留下完整 trace。

### 验证

```powershell
uv run pytest tests/sidecar/test_task_methods.py tests/cli/test_task_commands.py -q
```

---

## 7. trace 反查(B1 phase 4 起)

```powershell
uv run chariot trace list                                        # 列最近 turn
uv run chariot trace list --agent-profile researcher             # (B1 当前用 task / conversation 过滤)
uv run chariot trace list --task <task-id>
uv run chariot trace view <turn-id>                              # 树形展开 provider + tool + checkpoint
```

### 验证

```powershell
uv run pytest tests/platform/test_trace_foundations.py tests/platform/test_trace_writer.py tests/agent/test_trace_integration.py tests/sidecar/test_trace_methods.py tests/cli/test_trace_commands.py -q
```

---

## 7.5 eval 跑批 / baseline diff(B2 wave 1-5)

跑 golden task 集合,落基线,改完代码再跑同一套对比 verdict 变化。eval 内部用
唯一 conversation_id 串通到 B1 trace,所以每条 record 都能反查 trace turn。

```powershell
# 看现有 golden task(默认从 tests/golden/ 加载)
uv run chariot eval list-tasks

# 跑全套 → 自动落 ~/.chariot/eval/<timestamp>/(5 文件:meta/tasks/records/summary/report)
uv run chariot eval

# 只跑某类 / 单个 task
uv run chariot eval --category file
uv run chariot eval --task file_read_pyproject

# 走指定 agent_profile(走 _resolve_binding 三件套)
uv run chariot eval --agent researcher

# 试跑但不落盘
uv run chariot eval --no-save

# 列历史 run(看 summary)
uv run chariot eval list-runs

# 看单次 run 的 report.txt
uv run chariot eval show <run-id>

# baseline diff —— 跑完拿最新结果对比 <baseline-run-id>;
# 输出 REGRESSED / RECOVERED / NEW / REMOVED / CHANGED(STABLE 隐藏降噪)
uv run chariot eval --baseline <baseline-run-id>
```

**典型 review 流程**:

1. 改代码前:`chariot eval` → 拿到 `run_a`(基线)
2. 改完代码:`chariot eval --baseline run_a` → 看变化
3. 若有 REGRESSED 行,记下 task_id,然后:
4. `chariot trace list --conversation <eval-task-的-convo>`(或在桌面 Evals
   页直接点 turn_id 跳 Traces 页)→ 树形展开,排查 provider / tool 哪一步出问题

桌面 UI 同款链路:Evals 页 → 选 run → 选 baseline → 点 "Diff vs baseline" →
点 record 行的 turn_id → 跳 Traces 页。

### 验证

```powershell
uv run pytest tests/sidecar/test_eval_methods.py -q
```

---

## 7.6 conversation 全文搜索(B3 wave 1)

跨 conversation 模糊查历史消息。v18 起 `messages_fts`(SQLite FTS5)自动跟
`messages` 表同步;`append_message` 抽 anthropic blocks 的 text-only 入索引,
所以查 "type" / "tool_use" 等 schema 关键字不会有假阳性。

```powershell
# 全局搜索(默认 limit 20,bm25 排序)
uv run chariot conversation search "类型注解"
uv run chariot conversation search "Sandbox"

# 只搜某个 conversation 内
uv run chariot conversation search "research notes" --conversation 01KRBMJ4K07AJWPV4EKN513P0A
uv run chariot conversation search "Sandbox" --conversation 01KRBMJ4K07AJWPV4EKN513P0A

# 限制返回数量
uv run chariot conversation search "fts5" --limit 5
uv run chariot conversation search "shell" --limit 5

# 灾备:FTS 索引跟 messages 表不同步时(测试漏调 sync / 早期版本 bug),全量重建
uv run chariot conversation rebuild-fts
```

输出格式(终端):

```
- msg_id=01HXXX...  conv=01HCONV...  role=user
    rank=-0.83  snippet=…我想加一个 <mark>类型注解</mark> 检查 …
```

`<mark>` 是 FTS5 `snippet()` 内置的命中标记,默认 ±12 token 上下文。CLI
直接打印含 `<mark>...</mark>` 字面量,桌面端 wave 4 起把 mark 渲染成高亮。

FTS5 表达式速查(透传给 SQLite,不做语法糖):
- `hello world`  ≈ AND(同时含 hello + world)
- `hello OR world`
- `"hello world"`  短语(顺序敏感)
- `hello*`  前缀(`hello` / `hellos` / `hello_world` 都命中)
- `hello NOT world`

### 验证

```powershell
uv run pytest tests/platform/test_conversation_search.py tests/sidecar/test_conversation_search_methods.py -q
```

---

## 7.7 Context 自动压缩(B3 wave 2)

长对话越跑 prompt 越大。chariot 在送给 provider 之前估 token,过 70%
context_length 阈值就调 AuxiliaryClient 把最早 N turn 摘要成
`[context-summary] ...`。失败 fallback 直接丢最早一对 user/assistant turn。

`auxiliary_clients` 表(v19 起,seed 一条 `summarizer` 指向 `mock`,开箱即用):

```powershell
# 列已配置的 aux client(默认有一条 'summarizer' → mock)
uv run chariot auxiliary list

# 想用真实 claude 做摘要:删了默认 mock,再 add 一条同名 summarizer
uv run chariot auxiliary rm summarizer
uv run chariot auxiliary add --name summarizer --provider claude `
    --model claude-haiku-4-5-20251001 --param max_tokens=256 --param temperature=0.3

# 改 params(整体替换);改 model(`--model -` 表 clear)
uv run chariot auxiliary update summarizer --param max_tokens=512
uv run chariot auxiliary update summarizer --model -    # 回到继承 provider 的 model

# 跑一段长对话(20+ turn,单 turn 大 prompt)后看 trace 里压缩落地
uv run chariot trace list --limit 5
# 找一条 meta.context_compressed=true 的 turn,view 看 [context-summary] 插入位置
uv run chariot trace view <turn-id>
```

压缩透明(主调用方不感知);只有 `summarizer` 这一条 entry 名字会被 AIAgent
bootstrap 时认得 —— 其它名字的 aux client 可以共存,但当前 wave 不用;预留给
B4 critic / B5 guardrails 等副任务路由。

### 验证

```powershell
uv run pytest tests/platform/test_auxiliary_repo.py tests/platform/test_auxiliary_client.py tests/platform/test_context_compressor.py tests/platform/test_context_compression_e2e.py tests/sidecar/test_auxiliary_methods.py -q
```

---

## 7.8 `@reference` 解析(B3 wave 3)

user 消息里 `@file:` / `@diff:` / `@url:` / `@session:` 自动展开成
`<reference type=... key=...>...</reference>` 块,agent 拿到包好内容的
消息。

```powershell
# @file:展开当前 cwd 子树下的文件
uv run chariot chat "@file:README.md 给我一句话总结"

# @diff:HEAD~3 把最近 3 个 commit 的 diff 展开
uv run chariot chat "@diff:HEAD~3 这几个 commit 改了什么?"

# @url:走 http_get 工具同款 allowlist
uv run chariot chat "@url:https://example.com 这个站点干啥用的?"

# @session:加载历史 conversation
uv run chariot chat "@session:01HABCDE 接着上次思路继续"
```

错误时 reference 还在,只是 content 空 + 带 error 属性:

```xml
<reference type="file" key="ghost.md" error="not found"></reference>
```

agent 收到这个还能讲"这文件不存在,要不要我新建?",不像 silent drop。

**安全限制**:
- `@file:` 限 cwd 子树(防 `../../etc/passwd`)
- `@url:` 继承 `http_get` 工具的 allowlist
- `@diff:` subprocess 限超时 + 输出截断
- `@session:` 仅当前 user 可见会话(多租户上线后强制 owner 过滤)

### 验证

```powershell
uv run pytest tests/platform/test_reference_resolvers.py tests/platform/test_reference_expander.py tests/platform/test_reference_e2e.py -q
```

---

## 7.9 桌面 Conversations 页(B3 wave 4)

新增 `/conversations` 路由(`packages/app/src/pages/Conversations.tsx`):

- **列表区**:所有 conversation,按 `updated_at` 降序,显示 id / title /
  last_model / 消息数;点 "Open" 折叠展开详情(底部出 messages 全文)
- **顶部搜索框**:
  - 详情未展开 → 全局搜索(调 `search_conversation` RPC,无 `conversation_id`)
  - 详情已展开 → 搜索 scope 自动 narrow 到该 conversation(`conversation_id` 传入)
- **hit 卡片**:展示 snippet(包含 FTS5 `<mark>` 高亮)+ bm25 rank;点击 hit
  自动 open 对应 conversation 详情
- **Rebuild FTS 按钮**:调 `rebuild_conversation_fts`,index 跟 messages 不同步
  时灾备用(`chariot conversation rebuild-fts` 的桌面端等价)

注:hit snippet 用 `dangerouslySetInnerHTML` 渲染 `<mark>` 标签;FTS5 snippet
不会引入用户可控 HTML(只标记关键词),不需要额外 sanitization。

### 验证

```powershell
cd packages/app && bun run typecheck && bun run build
```

---

## 7.10 Critic 基建(B4 wave 1)

Critic 是一个"裁判 LLM",对主 agent 的产出强制输出 `VERDICT: PASS|FAIL|UNSURE`
一行 + reason。复用 B3 wave 2 的 `auxiliary_clients` 表 —— 加一行 `name='critic'`
就装好。

```powershell
# 推荐用一个独立 budget 的 critic(便宜 + 受控温度)
uv run chariot auxiliary add --name critic --provider claude `
    --model claude-haiku-4-5-20251001 --param max_tokens=512 --param temperature=0.2
uv run chariot auxiliary add --name critic --provider ollama-qwen --model qwen2.5:1.5b --param max_tokens=512 --param temperature=0.2

# 手动跑一次 critique:看 prompt / verdict 解析效果
uv run chariot critic try "写一个排序函数" --produced "def sort(x): return x"
# 期望输出:
#   verdict: FAIL
#   reason:  没有实现实际排序,直接返了入参
```

VERDICT parser 兜底:critic 输出**不符合契约**时 → verdict=UNSURE,reason 记原始
输出前 200 char(给人复盘 critic prompt 是不是要调)。

### 验证

```powershell
uv run pytest tests/platform/test_critic_agent.py tests/sidecar/test_critic_methods.py -q
```

---

## 7.11 Reflect-then-retry(B4 wave 2)

工具失败 / 主 agent 自报 fail / 流式 error → critic 介入 → 把 verdict + reason
作为 `[REFLECTION]` 块追加到对话末尾 → 主 agent 重试,直到 PASS / UNSURE /
retry 预算用尽 / circuit breaker 触发。

```powershell
# 显式开 reflection 跑一次任务(reflect 是 chat 的 flag,不是子命令)
uv run chariot chat --reflect --reflect-retries 3 "写一个 O(n log n) 排序"

# 看 trace:每一次 retry 跑一个独立的 trace_turn,第二轮起 meta 含 reflection_iteration
uv run chariot trace list --limit 5
uv run chariot trace view <turn-id>
# 期望第二条 trace turn 的 meta 形如:
# {
#   "reflection_iteration": 1,
#   "reflection_trigger": "self_report_fail",   // 或 tool_failure / error_event
#   "reflection_previous_verdict": "FAIL",
#   "reflection_previous_reason": "缺实现细节..."
# }
```

**触发优先级**:`error_event` > `tool_failure` > `self_report_fail`(同一轮多
信号命中时,只记最高优先级一个)。

**双层防护**:
- `--reflect-retries`(默认 2):本次任务最多反思几次;0 = 关
- circuit breaker:连续 3 轮拿到 FAIL → 跳出,避免反思死循环耗预算

**UNSURE 不消耗 retry**:critic 给不了明确信号(产出格式不符 / 输出为空)→
直接停止,不浪费预算。

### 验证

```powershell
uv run pytest tests/platform/test_reflection_loop.py tests/platform/test_reflection_e2e.py -q
```

---

## 7.12 agent_profile reflection 开关 + 桌面 UI(B4 wave 3)

reflection 默认**关**(全局 + per-agent);要开,在 agent_profile 上显式打开:

```powershell
# 全局先确保 critic aux client 装好(7.10 已建);然后给特定 agent 开 reflection
uv run chariot agent update trace_demo --reflection-on --reflect-retries 3

# 跑该 agent,reflection 自动生效(无需再加 --reflect)
uv run chariot chat --agent trace_demo "写一个 O(n log n) 排序"

# 关掉
uv run chariot agent update trace_demo --reflection-off
```

字段优先级:`req.reflection_enabled=True`(CLI `--reflect`)> agent_profile
`reflection_enabled=True`。两者都关 → 单轮路径。

**Agents 桌面页**新增字段:
- `reflection_enabled` 复选框
- `reflection_max_retries` 输入框

**Traces 桌面详情页**:trace_turns.meta 含 `reflection_iteration` 字段时,在
turn 详情下方插 "Reflection" panel,显示触发原因 + 上一轮 verdict + reason。

### 验证

```powershell
uv run pytest tests/platform/test_agent_profile_reflection.py -q
cd packages/app && bun run typecheck && bun run build
```

---

## 7.13 Guardrails(B5 wave 1)

工具调用拦截层 —— 13 条内置规则盯 `shell_exec` / `write_file` / `http_get`
等工具的参数,危险动作(`rm -rf` / `chmod 777` / `git push --force` / DB drop / ...)
直接 DENY 或 REQUIRE_APPROVAL。

```powershell
# 列规则 + 当日配额剩余
uv run chariot guardrail list

# dry-run 评估某次调用会不会被拦
uv run chariot guardrail try shell_exec --args '{"command":"rm -rf /tmp/x"}'
# 期望:verdict=DENY  rule=shell_rm_rf

uv run chariot guardrail try shell_exec --args '{"command":"chmod 777 /opt"}'
# 期望:verdict=REQUIRE_APPROVAL  rule=shell_chmod_unsafe  quota_remaining=5
```

实际跑 chat 时,被拦的工具调用直接进 `tool_result(is_error=True)`,主 agent
读到 'guardrail denied: ...' 文本 → 自己改 plan(配 B4 reflection 后还会触发
critic 重试)。

**配额机制**:per-rule `daily_quota`(UTC 0 点 reset);超限 → 自动降级为 DENY。
DENY 类不带配额(永远拒)。

### 验证

```powershell
uv run pytest tests/platform/test_guardrail_rules.py tests/platform/test_guardrail_engine.py tests/platform/test_guardrail_integration.py tests/sidecar/test_guardrail_methods.py -q
```

---

## 7.14 Audit 自动 hook(B5 wave 2)

六类自动 event 落 `audit_events` 表,主流程无感(`AuditHookManager` 在
ToolExecutionService / MemoryRepo 包装层 / wave 3 CheckpointManager 上自动
触发):`tool_call_pre` / `tool_call_post` / `guardrail_verdict` /
`memory_store` / `checkpoint_create` / `rollback`。

写入是 best-effort —— 失败只 warn log,不阻断 tool 调用 / memory 操作。
sessionmaker=None 时整个 manager 退化为 no-op(测试路径不污染主路径)。

```powershell
# 列最近 50 条 audit 事件
uv run chariot audit list

# 限制条数
uv run chariot audit list --limit 20

# 展开单条 event 的 payload(JSON)
uv run chariot audit show <event_id>

# tail = list 别名,语义更明确
uv run chariot audit tail --limit 10
```

桌面/sidecar 路径用 `list_audit_events` / `get_audit_event` JSON-RPC。

跟 B1 trace 关系:trace_turns 是 "每个 turn 一行";audit_events 是 "每个细粒度
事件一行"。两者用 tool_use_id 串通(`tool_call_pre/post`/`guardrail_verdict`
都带 tool_use_id 字段)。

**验证**:

```powershell
# 单元 + 集成测试(AuditHookManager 6 类事件 + ToolExecutionService 集成)
uv run pytest tests/platform/test_audit_hooks.py -q

# sidecar list_audit_events / get_audit_event RPC + memory_store hook 落库
uv run pytest tests/sidecar/test_audit_methods.py -q
```

---

## 7.15 Checkpoint + Capability gating(B5 wave 3)

`chariot checkpoint create <name>` 三件套打 snapshot:git stash + SQLite
backup + `~/.chariot/{config.yaml,.env}` tarball。`rollback` 走反向。每段独立
try/except,best-effort —— 某段失败 / no-op 不阻其它段;`checkpoint_create` /
`rollback` 事件自动写 `audit_events`。

```powershell
# 全量 snapshot
uv run chariot checkpoint create before_refactor

# 列所有 checkpoint(显示 git/db/config 三段是否 ok)
uv run chariot checkpoint list

# 看单条详情(git stash ref / sqlite 文件 / tarball 路径 / errors)
uv run chariot checkpoint show <id>

# rollback 全量(三段反向,失败的段在 errors 报告)
uv run chariot checkpoint rollback <id>

# 删除(rm 落盘 sqlite/tgz + DB 记录;git stash 保留)
uv run chariot checkpoint delete <id>
```

**Capability gating**(默认全关;v21 migration 种入 `capabilities` 表):

```powershell
# 看当前 capabilities(name / enabled / updated_at)
uv run chariot capability list

# 打开 self-mod(允许 agent 改 chariot/ 下文件;guardrail 改判 REQUIRE_APPROVAL)
uv run chariot capability enable enable_self_mod

# 关掉
uv run chariot capability disable enable_self_mod
```

**`--yolo` 全局 flag**(per-process,不持久化;不写 DB 的 `yolo` 行):

```powershell
# 跳过所有 REQUIRE_APPROVAL,但不影响 DENY(危险动作仍拒)
uv run chariot --yolo chat "..."
```

⚠️ `--yolo` **只用于沙箱机器 / CI**;生产 / 本地开发不建议。即使 `--yolo`,
guardrail 仍写 audit,只是不阻断。

sidecar 路径:`list_checkpoints` / `get_checkpoint` / `create_checkpoint` /
`rollback_checkpoint` / `delete_checkpoint` + `list_capabilities` /
`set_capability`。

**验证**:

```powershell
# step 1:Capabilities + CapabilityRepo + gate 翻转 + ApprovalPolicy 接 caps
uv run pytest tests/platform/test_capabilities.py -q

# step 2:CheckpointManager 三件套 + rollback + delete + audit hook
uv run pytest tests/platform/test_checkpoint_manager.py -q

# step 3:无独立测试套,但 guardrail / audit / sidecar 套件回归确保接线没破
uv run pytest tests/platform/test_guardrail_integration.py tests/platform/test_audit_hooks.py tests/sidecar/test_audit_methods.py -q
```

---

## 7.16 桌面 Security 页(B5 wave 4)

新增 `/security` 路由,Tabs 三标签:

- **Guardrails 标签**:规则列表 + 当日配额剩余 + 顶部 "Test rule" 小工具
- **Audit 标签**:event timeline(降序)+ 按 type/status 筛 + 点行展开 payload
- **Checkpoints 标签**:列表 + "Create checkpoint" 按钮 + 每行 Rollback / Delete
  (带二次确认)

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
