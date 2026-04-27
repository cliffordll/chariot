# Chariot 路线图

0.1.0 ~ 0.3.1 已落地的范围见 `docs/FEATURE.md` + `docs/history/`。本文档记录
**未来方向**(0.4.0 起)。

---

## v1:真实模型接入 / 选型 ✅(0.2.0 ~ 0.3.1 完成)

- ✅ **Model 配置体系**:0.2.x 用 `~/.chariot/config.toml`,0.3.0 起改 DB-backed
  (`models` 表);0.3.1 加 `params` 列承载 runtime sampling 默认值
- ✅ **AnthropicModel**:透传到 Anthropic Messages API,错误映射 + 流式 SSE
- 🟡 **OpenAIAdapterModel** / **LocalLlamaModel**:暂未上(用 LiteLLM 等外部转换器
  接 chariot 当 Anthropic 后端即可)
- ✅ **Model 路由**:0.3.1 路由模型重构 —— active 概念退役,client 在 body.model 写
  entry name 直接路由;Chat 页 entry 选择 + localStorage 持久化;Models 页行内拆开
  options 主键 + 行展开 ParamsEditor;CLI `chariot model list / probe / add / edit /
  rm / duplicate`

## v2:Agent 进化(chariot 的核心方向)

- **多轮对话记忆** —— Agent 持有 `Conversation` 状态,跨轮复用;配套 `conversations`
  / `messages` 表
- **工具调用 / function calling** —— Agent 在 respond 后检测 tool_use,执行工具,
  拼新 turn 再调 Model
- **自我进化循环** —— 读 logs 表 feedback,调权重 / 切换 model / 修 prompt;
  `agent.evolve()` 定期任务
- **多 Agent 实例** —— logs 加 `agent_id` 字段;`get_agent(agent_id)` 按 ID 路由;
  config 支持定义多个 agent profile

## 数据面体验

- **Chat 会话持久化** —— `conversations` + `messages` 表 + `/admin/conversations/*`
  端点 + GUI 侧栏会话列表、历史翻阅、会话导出
- **Chat 原始请求 / 响应预览面板** —— Chat 页可折叠 JSON 面板,显示每轮请求体 +
  响应体(含 SSE 完整事件序列)。协议调试用
- **CLI Chat 增强** —— 多会话文件(`chariot chat --session foo`,
  `~/.chariot/sessions/*.json`)、会话导入导出

## 管理面体验

- 实时日志流(SSE / WebSocket 而非 polling)
- 按 model / protocol 切分的用量统计(时间序列图)
- 配置导入导出(`chariot model export/import` —— 把 DB 里的 entries 导出为 JSON / 从 JSON 导入)

## 发版与分发

- **自动更新真实启用**(v0 代码已合入 tauri-plugin-updater,但 pubkey 占位未替换)
- **代码签名**(Authenticode / Apple Developer 证书)
- 跨平台打包(macOS / Linux);`chariot-server.spec` 目前仅 Windows 验证
- 首个 Release 闭环(tag → CI → 签名 installer → latest.json → updater)

## 运维 / 扩展

- 请求日志 TTL 清理策略
- 多用户账户(多台机器共享一个 server 实例)
- 多语言(i18n)
