# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史版本见 `docs/history/<version>/DEVELOPMENT.md`。
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不得改写内容或丢失信息。

## 当前阶段

当前只推进 `Milestone A1: Prompt system`。

目标是把现有的 prompt 组装逻辑从“隐式拼接”收口成可检查、可追踪、可查询的 prompt system，让一次 turn 的最终 prompt 由哪些层组成、从哪里注入、最终版本是什么，都能被稳定复现。

### 本阶段交付

- `prompt bundle`：定义 prompt 组合单元，承载不同角色和模式下的 prompt 结构。
- `prompt version`：记录 prompt 的版本化快照，便于回溯和对比。
- `prompt trace`：记录一次 turn 的最终 prompt 组成、来源和大小。
- 最小管理入口：CLI 可以新增、更新、激活 bundle/version，并查看 bundle、version 和单次 turn 的 prompt 组成。

### 工作原理

这一阶段不是先做一个“更复杂的 prompt 生成器”，而是先把 prompt 组装这件事收口成一个可观察的边界。

- `AIAgent` 仍然负责真正的 turn 调度和 provider 调用。
- `prompt system` 负责记录“这一次 turn 是由哪些层组成的、这些层从哪里来、用了哪个 bundle/version”。
- `prompt bundle` 是 prompt 的组成与装配定义，类似把 `CLAUDE.md`、系统说明、运行时上下文、工具说明等按层组装起来。
- `prompt version` 固化 bundle 在某个时刻的快照，方便回溯和对比。
- `prompt trace` 记录一次 turn 的实际输入快照、分层来源和体积信息。

这样做的目的，是把原来隐式拼接的 prompt 变成可查询、可复现、可回看的一条链路。当前阶段先保证“能看清”，后续再考虑“如何自动压缩、摘要或重排”。

### Prompt Bundle 结构

`prompt bundle` 是一组按层组织的 prompt 配方。它本身不等于最终发给模型的文本，而是描述这次 turn 要用哪些层、每层从哪里来、各自负责什么。

- `base_system`：系统级基础规则，定义整个 assistant 的底层行为约束。
- `developer`：开发者级说明，通常来自仓库规范、协作约定、`CLAUDE.md` 之类的工作规则。
- `runtime`：运行时注入内容，例如当前 conversation 状态、入口参数、任务上下文。
- `memory`：从 memory platform 注入的长期或短期记忆片段。
- `skill`：当前 turn 需要启用的 skill 说明或技能指令。
- `tool_instruction`：工具使用说明，告诉模型有哪些工具以及怎么调用。
- `tool_choice`：工具选择约束，描述这次 turn 是否允许或优先使用某些工具。
- `thinking`：内部推理或思考相关提示，仅在需要时参与组装。

这几层的原则是：
- 层级稳定，职责单一。
- 内容来源可追踪。
- 最终 prompt 由 bundle 结构 + 当前 turn 上下文一起组装，而不是在业务代码里散拼。

如果新建 bundle 时不确定 `layer` 怎么填，按这个顺序处理：
- 第一选择：先不要传 `--layer`，直接让系统使用默认 layers。
- 第二选择：先执行 `prompt show default` 或 `prompt version default v1`，看默认 bundle 的结构，再照着复制修改。
- 第三选择：只有在你明确要重写整套 prompt 结构时，才自己传完整 `--layer`。

注意：
- 传了 `--layer`，就表示你在显式定义这个 bundle 的 layers，系统不会自动把默认 layers 再补进去。
- 所以新建时如果只是想先有一个可用 bundle，最稳妥的做法就是先不传 `--layer`。
- 后面再用 `prompt update` 逐步改 layers，会比一开始就手写完整结构更稳。
- 桌面端 Prompt 页也提供了 `Fill default template` 按钮，方便先填一个可编辑模板。

最小示例：

```powershell
uv run chariot prompt add demo-cn --description "中文演示 prompt"
uv run chariot prompt show default
uv run chariot prompt update demo-cn --layer '{"name":"developer","source":"manual","content":"你需要用中文回答，并且保持简洁。"}'
```

### 时序说明

这套 prompt system 可以拆成两条主线：

- 运行流：一次聊天 turn 如何拿到当前 active prompt bundle，并把 trace 留下来。
- 管理流：如何通过 CLI 或桌面端查看、编辑、激活 bundle，以及查看 trace。

#### 运行流

```text
User
  -> CLI / Desktop UI
  -> AIAgent
  -> PromptRepo.get_active_bundle()
  -> AIAgent 将 bundle layers 叠进 request.system
  -> AIAgent 做 provider normalization
  -> Provider
  -> Provider stream: ChatEvent / token / tool event
  -> AIAgent 实时转发事件给 UI
  -> Provider 完成响应
  -> PromptRepo.record_trace(request, bundle, version, provider, model)
  -> Trace Store
  -> 返回 trace_id
  -> UI 显示回答
```

要点：
- `AIAgent` 负责 turn 调度和最终请求组装。
- `PromptRepo` 负责取 active bundle、记录 trace。
- `Provider` 只关心执行请求，不关心 bundle 来源。
- `Trace Store` 记录这次 turn 的 prompt 组成和版本。

#### 管理流

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> PromptService
  -> PromptRepo
  -> SQLite DB
```

按功能拆开看：
- 查看
  - `list_prompt_bundles`
  - `get_prompt_bundle`
  - `list_prompt_versions`
  - `get_prompt_version`
  - `list_prompt_traces`
  - `inspect_prompt`
- 新建
  - `add_prompt_bundle(name, description, layers)`
  - 插入 bundle 和初始 version
- 更新
  - `update_prompt_bundle(name, description?, layers?)`
  - 更新 bundle 并生成新 version
- 激活
  - `activate_prompt_bundle(name, version?)`
  - 更新 active bundle / active version 标记
- 查 trace
  - `list_prompt_traces`
  - `inspect_prompt`

要点：
- CLI 和桌面端只是入口，不直接操作数据库。
- `Sidecar` 负责 RPC 适配和参数整理。
- `PromptService` 负责业务流程。
- `PromptRepo` 负责数据库读写。

### 本阶段范围

- base system prompt
- developer prompt
- runtime prompt
- memory prompt
- skill prompt
- tool instruction prompt
- prompt 组装顺序
- prompt 追踪信息

### 本阶段不做

- `Milestone A2: Context management`
- `Milestone A3: Memory platform`
- `Milestone A4: Tool management`
- `Milestone A5: Provider management`
- `Milestone A6: Agent and task management`
- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`

### 执行顺序

1. 先落 `prompt_bundles` / `prompt_versions` / `prompt_traces` 的最小 schema 和 repo。
2. 再把现有 prompt 组装逻辑收进统一入口，保证最终 prompt 可追踪。
3. 补 CLI 和 sidecar 的最小查询命令。
4. 补测试和一个可跑通的 smoke demo。
5. 确认 A1 收口后，再切换到 `docs/LONGTERMPLAN.md` 中的下一阶段。

### 验收标准

- 一次 turn 结束后，能查询该 turn 的 prompt trace。
- trace 里能看到 bundle/version、request 快照、source refs 和 prompt size。
- `prompt list/show/versions/version/traces/inspect` 能查到 bundle、版本和单次 turn 的记录。
- prompt 的层次定义稳定，后续版本可以在 bundle/version 上演进，而不是散落在业务代码里。
- 现有的核心 smoke 测试不回退。

### 验收方法

先跑自动化验证，再做一次手工检查。

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/agent/test_prompt_system.py -q
```

然后执行一次中文演示，确认 prompt 的管理和 trace 都正常：

```powershell
uv run chariot chat --conversation new "请简要介绍一下你自己"
uv run chariot prompt list
uv run chariot prompt add demo-cn --description "中文演示 prompt" --layer '{"name":"developer","content":"你需要用中文回答，并且保持简洁。"}'
uv run chariot prompt update demo-cn --description "中文演示 prompt v2" --layer '{"name":"runtime","content":"当前演示场景：prompt 管理验收。"}'
uv run chariot prompt activate demo-cn
uv run chariot prompt show default
uv run chariot prompt versions default
uv run chariot prompt version default v1
uv run chariot prompt traces
uv run chariot prompt inspect <trace_id>
```

中文演示建议保持在同一行复制，避免 PowerShell 把 JSON 打断：

```powershell
uv run chariot prompt add demo-cn --description "中文演示 prompt" --layer '{"name":"developer","content":"你需要用中文回答，并且保持简洁。"}'
uv run chariot prompt update demo-cn --description "中文演示 prompt v2" --layer '{"name":"runtime","content":"当前演示场景：prompt 管理验收。"}'
uv run chariot prompt activate demo-cn
```

英文演示也保留一版，方便验证多语言场景：

```powershell
uv run chariot prompt add demo-en --description "English demo prompt" --layer '{"name":"developer","content":"Answer in English and keep it concise."}'
uv run chariot prompt update demo-en --description "English demo prompt v2" --layer '{"name":"runtime","content":"Current demo scenario: prompt management verification."}'
uv run chariot prompt activate demo-en
```

这段演示的含义是：

- 先发起一次中文聊天，确认 runtime 会写入 trace。
- 再查看默认 bundle，确认当前激活的 prompt 结构可见。
- 新建一个中文命名的 bundle，验证 `add` 能落库。
- 更新这个 bundle，验证 `update` 会生成新 version。
- 激活这个 bundle，验证运行时会切换当前 active prompt。
- 再用英文 bundle 重复一次同样流程，确认 prompt system 不依赖中文文案。
- 最后查 `versions` 和 `inspect`，确认版本和 trace 都能回看。

手工验收时重点看四件事：

- `prompt add/update/activate` 能否创建并切换可用 prompt。
- `prompt list/show/versions/version/traces` 能否看到 bundle、版本和 trace。
- `prompt inspect` 能否看到这次 turn 的 request、source refs 和 prompt size。
- 这条 trace 是否和实际聊天行为对应，而不是一条孤立的配置记录。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把后续闭环能力塞进来。
- 新计划必须在用户确认后再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。
