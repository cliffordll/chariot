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
- 最小查询入口：CLI / sidecar 可以查看 bundle、version 和单次 turn 的 prompt 组成。

### 工作原理

这一阶段不是先做一个“更复杂的 prompt 生成器”，而是先把 prompt 组装这件事收口成一个可观察的边界。

- `AIAgent` 仍然负责真正的 turn 调度和 provider 调用。
- `prompt system` 负责记录“这一次 turn 是由哪些层组成的、这些层从哪里来、用了哪个 bundle/version”。
- `prompt bundle` 描述可配置的 prompt 结构。
- `prompt version` 固化 bundle 在某个时刻的快照，方便回溯和对比。
- `prompt trace` 记录一次 turn 的实际输入快照、分层来源和体积信息。

这样做的目的，是把原来隐式拼接的 prompt 变成可查询、可复现、可回看的一条链路。当前阶段先保证“能看清”，后续再考虑“如何自动压缩、摘要或重排”。

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
- `prompt list/show/inspect` 能查到 bundle、版本和单次 turn 的记录。
- prompt 的层次定义稳定，后续版本可以在 bundle/version 上演进，而不是散落在业务代码里。
- 现有的核心 smoke 测试不回退。

### 验收方法

先跑自动化验证，再做一次手工检查。

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/agent/test_prompt_system.py -q
```

然后执行一次聊天，再看 prompt 记录：

```powershell
uv run chariot chat --conversation new "请简要介绍一下你自己"
uv run chariot prompt list
uv run chariot prompt show default
uv run chariot prompt versions default
uv run chariot prompt version default v1
uv run chariot prompt traces
uv run chariot prompt inspect <trace_id>
```

手工验收时重点看三件事：

- `prompt list/show/versions/version/traces` 能否看到 bundle、版本和 trace。
- `prompt inspect` 能否看到这次 turn 的 request、source refs 和 prompt size。
- 这条 trace 是否和实际聊天行为对应，而不是一条孤立的配置记录。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把后续闭环能力塞进来。
- 新计划必须在用户确认后再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。
