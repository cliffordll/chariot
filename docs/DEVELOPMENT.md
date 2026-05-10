# Development

> 本文只描述当前开发计划。长期路线见 `docs/EVOLUTION_PLAN.md`，历史版本见 `docs/history/<version>/DEVELOPMENT.md`。
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不得改写内容或丢失信息。

## 当前阶段

当前只推进 `Milestone A1: Prompt system`。

目标是把现有的 prompt 组装逻辑从“隐式拼接”收口成可检查、可追踪、可查询的 prompt system，让一次 turn 的最终 prompt 由哪些层组成、从哪里注入、最终版本是什么，都能被稳定复现。

### 本阶段交付

- `prompt bundle`：定义 prompt 组合单元，承载不同角色和模式下的 prompt 结构。
- `prompt version`：记录 prompt 的版本化快照，便于回溯和对比。
- `prompt trace`：记录一次 turn 的最终 prompt 组成、来源和大小。
- 最小查询入口：CLI / sidecar 可以查看 bundle、version 和单次 turn 的 prompt 组成。

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
5. 确认 A1 收口后，再切换到 `EVOLUTION_PLAN.md` 中的下一阶段。

### 验收标准

- 一次 turn 结束后，能查询最终 prompt 的组成和来源。
- prompt 的注入顺序稳定且可复现。
- prompt 版本和 trace 可查。
- 现有的核心 smoke 测试不回退。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把后续闭环能力塞进来。
- 新计划必须在用户确认后再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。
