# Chariot 架构设计(0.5.0)

> **当前版本**:`0.5.0`(开发中)
> **上一版归档**:[`docs/history/0.4.0/DESIGN.md`](history/0.4.0/DESIGN.md)
>
> **0.5.0 主题**:**协议级流式工具循环** —— Agent 全程 streaming,工具调用 /
> 结果在 SSE 里实时出现,CLI / UI / 透传客户端共享一份原生 Anthropic 事件流。
>
> **关键变更**(vs 0.4.0):
> - **撤掉 `Agent.handle` 的 `stream=False` 中间步 + `stream=True` 收尾重发**:
>   原本 slow path 用非流式跑工具循环,收敛后再单独发一次 stream=True 给 client
>   (代价:多调一次 LLM,client 看不到中间 turn)。0.5.0 改为全程 streaming,
>   一条 HTTP 响应里串多个 `message_start ... message_stop` 块,server 在轮间
>   合成 `tool_result` 消息块
> - **SDK `ChatStream` 加 typed events**:除原 `text_deltas` 外,新增
>   `events()` 吐 `text` / `tool_use_start` / `tool_use_input` / `tool_use_complete` /
>   `tool_result` / `turn_complete` 类事件,供 CLI / UI 渲染中间状态
> - **CLI REPL 渲染中间事件**:dim 灰行打印 `→ read_file({"path": "..."})` /
>   `← 1.2 KB` / `← error: ENOENT`
> - **UI Chat 页 pending 卡片实时 append `tool_use` / `tool_result` blocks**:
>   不再依赖 turn 结束后 `loadConvDetail` 重拉才看到工具
> - **跨客户端协调(配套修)**:
>   - server 加 conversation 级 advisory lock(`load history → append → call model`
>     包成 critical section),解决并发写时 model 看到错乱上下文
>   - SDK 加 `get_conversation_messages(id)`,CLI 在每轮 send 前可选 fetch 最新
>     history,解决 CLI 不知 UI 写入的盲区
>
> **0.4.0 关键变更**(沿用,见归档):多轮对话记忆(`conversations` / `messages`
> 表 + `X-Chariot-Conversation` header)+ 工具调用(`Tool` ABC + 4 内置工具 +
> Agent 工具循环)。

---

## 1. 一句话总结

沿用 0.4.0,见 [`history/0.4.0/DESIGN.md` §1](history/0.4.0/DESIGN.md)。

## 2. 分层

沿用 0.4.0,见 [`history/0.4.0/DESIGN.md` §2](history/0.4.0/DESIGN.md)。Agent /
Model / Tool / Conversation 四层契约不变,只是 Agent.handle 内部从"非流式
循环 + 单次流式收尾"改成"全程流式循环",层间接口形态不动。

## 3. 唯一对外端点

沿用 0.4.0,见 [`history/0.4.0/DESIGN.md` §3](history/0.4.0/DESIGN.md)。
`/v1/messages` 端点不变,`X-Chariot-Conversation` header 语义不变。

## 4. 数据面 SSE 流(0.5.0 重写)

### 4.1 流形态:多 message 块串联

0.5.0 起一条 HTTP 响应里可以串多个 Anthropic `message_start ... message_stop` 块:

```text
event: message_start    ← turn 1 assistant 开始
event: content_block_start (type=text)
event: content_block_delta (text_delta)
event: content_block_stop
event: content_block_start (type=tool_use, id=toolu_01, name=read_file)
event: content_block_delta (input_json_delta: {"path":"...")
event: content_block_delta (input_json_delta: ".txt"})
event: content_block_stop
event: message_delta (stop_reason=tool_use)
event: message_stop      ← turn 1 收尾

[server-side: 执行 read_file(...)]

event: message_start    ← server 合成的 tool_result message(role=user)
event: content_block_start (type=tool_result, tool_use_id=toolu_01)
event: content_block_delta (text_delta: "<file contents>")
event: content_block_stop
event: message_stop

event: message_start    ← turn 2 assistant 开始(下一轮 LLM 回复)
...
event: message_stop

[直到 stop_reason != tool_use,server 关流]
```

**为什么选这个方案而不是自定义 event**:保留"chariot = Anthropic 协议透明代理"
的纯净度。透传客户端(claude code / 直接调 Anthropic SDK 的)能原生消费;chariot
自己的 SDK 只多挑几类事件,不需要 protocol upgrade。

### 4.2 server 合成 tool_result message 的形态

工具结果是 Anthropic 协议里 `role=user` 的 message,content 数组里 `type=tool_result`
block。server 在两轮 LLM 之间合成这条 message 并通过 SSE 流转给 client:

- `message_start.message.role = "user"`,`message.id` 用 server 自生成(`tu_${ulid}`)
- `content_block_start.content_block = {type: "tool_result", tool_use_id: "toolu_01", content: ""}`
- 工具输出文本通过 `content_block_delta.delta.type = "text_delta"` 增量推送
- 错误时 `content_block_start.content_block.is_error = true`(协议字段)

### 4.3 stop_reason / 终止判定

- LLM 给 `stop_reason = "tool_use"` → server 跑工具 → 合成 tool_result message →
  下一轮 LLM streaming
- LLM 给 `stop_reason = "end_turn"` 或别的非 tool_use → server 关流,不再发新轮
- `max_iter`(env `CHARIOT_MAX_TOOL_ITER`,默认 10)超 → server 写 `event: error`
  (Anthropic 协议原生)+ 关流

## 5. Model 接口契约(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §4-5](history/0.4.0/DESIGN.md)。Model 仍然无状态、不
碰 DB、不感知工具,`respond(body, stream)` 唯一变化是 0.5.0 起 Agent 永远传
`stream=True`,不再有 `stream=False` 调用。

## 6. 模型管理层(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §6](history/0.4.0/DESIGN.md)。

## 7. Conversation 层(沿用 0.4.0,见归档 + 0.5.0 加锁)

主体见 [`history/0.4.0/DESIGN.md` §7](history/0.4.0/DESIGN.md)。

**0.5.0 增量 · advisory lock**:并发场景两个 client 同时打同一 conversation_id
时,各自 `load history` 看到的是 send 之前的快照,没看到对方那条;然后各自调
Model + append 自己的 turn → DB 序列错乱。修复:

- `ConversationLockManager`(`chariot/server/conversation_lock.py`):per-conv
  in-memory `asyncio.Lock` 字典(单进程 server,async 协程在事件循环里串行,
  asyncio.Lock 足够);`acquire(conv_id, timeout_s)` 是 async context manager,
  `yield` 期间持锁,退出自动释放
- 等待中的 client 排队,先到先服务;超时(env `CHARIOT_CONV_LOCK_TIMEOUT_S`,
  默认 30s)→ 503 + `code: conversation_busy`,client 自行重试

实现位置:`Agent._stream_tool_loop` 把整段流式循环用 `async with
ConversationLockManager.acquire(conversation_id):` 包住(stateless 走
`nullcontext`,无锁)。

> **设计取舍**:原方案是 SQLite `BEGIN IMMEDIATE` 长事务做 DB-level lock
> (跨进程也安全)。0.5.0 选 in-memory asyncio.Lock 是因为 chariot 是单进程
> server,asyncio.Lock 实现简单、没事务冲突 / 死锁风险。多进程部署再切 SQLite
> 锁(0.6.0+ 选项)。

## 8. Tool 层(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §8](history/0.4.0/DESIGN.md)。Tool ABC / ToolRegistry /
4 内置工具实现 0.5.0 不变。

## 9. Agent 层(0.5.0 重写)

### 9.1 流程对比

```text
0.4.0(归档):
  ┌─ Stateful 起步:load history + persist new_msgs
  ├─ while iter < max_iter:
  │   resp = await model.respond(body, stream=False)   ← 非流式拿全文
  │   detect tool_use → execute → append tool_result
  │   no tool_use → break
  └─ 收敛:再调 await model.respond(body, stream=True) ← stream 重发给 client

0.5.0:
  ┌─ Stateful 起步:async with conv_repo.locked(conv_id):
  │     load history + persist new_msgs
  ├─ while iter < max_iter:
  │   stream = await model.respond(body, stream=True)
  │   async for event in stream:
  │       forward event 给 client
  │       buffer assistant content blocks
  │   message_stop 后判定:
  │     有 tool_use → execute tool → 合成 tool_result message → 写 SSE → 拼下轮 body
  │     无 tool_use → 关流 break
  └─ end of with(自动释放 lock)
```

### 9.2 关键实现点

- **stream proxying**:server 转 SSE 时不能简单 1:1 转发 —— 需要在 message_stop
  后插入 server 自己的判定逻辑,可能合成 tool_result message,可能新起一轮 LLM
- **content buffer**:为了 detect tool_use,server 必须 buffer 当前 turn 的
  assistant content blocks。`content_block_start(tool_use)` + 累积 `input_json_delta`
  → `content_block_stop` 后拿到完整 tool_use input
- **持久化时机**:每轮 message_stop 后立即持久化 assistant content + tool_result
  到 DB(确保 stream 中断时已发的 turn 不丢)
- **错误传播**:工具执行抛错 → 合成 `tool_result is_error=true` message 续流;
  Model 层 4xx/5xx → SSE 写 `event: error` + 关流

## 10. SDK / CLI 增量(0.5.0)

### 10.1 ChatStream typed events

`chariot/sdk/streams.py` 加 `events()` 方法:

```python
@dataclass(frozen=True)
class StreamEvent:
    kind: Literal["text", "tool_use_start", "tool_use_input", "tool_use_complete",
                  "tool_result", "turn_complete", "stream_done"]
    # kind 决定下面哪些字段非空
    text: str | None = None                      # text
    tool_use_id: str | None = None               # tool_use_*, tool_result
    tool_name: str | None = None                 # tool_use_start
    tool_input_partial: str | None = None        # tool_use_input(JSON 增量)
    tool_input: dict[str, Any] | None = None     # tool_use_complete
    tool_result_text: str | None = None          # tool_result
    is_error: bool = False                       # tool_result

async def events(self, resp) -> AsyncIterator[StreamEvent]: ...
```

`text_deltas` 保留作 thin wrapper(向后兼容 0.4.x caller)。

`input_tokens / output_tokens` 跨多 message_start 累加(`message_start` 给 input,
最后一个 `message_delta` 给最终 output_tokens)。

### 10.2 ChatContext 加 fetch_history

CLI 不知 UI 写入的盲区:`ChatContext.refresh_from_server()` 调
`client.get_conversation_messages(conv_id)` 把 `self.messages` 替换成 server
canonical state。REPL `_one_turn` send 前自动调一次(stateful 模式 only)。

trade-off:每轮多一次 GET。可设环境变量关闭(`CHARIOT_CLI_AUTO_REFRESH=0`)。

### 10.3 CLI REPL 渲染

`Renderer` 加 `tool_use_line(name, input_repr)` / `tool_result_line(text, is_error)`,
打 dim 灰输出。`ChatRepl._one_turn` 改用 `ctx.events()` 替代 `ctx.run_turn(on_token)`。

## 11. UI Chat 页增量(0.5.0)

### 11.1 流式渲染 tool blocks

`packages/app/src/lib/streams.ts` 同 SDK,parse 新事件类型 typed dispatch。

`packages/app/src/pages/Chat.tsx`:
- `pending` 状态从 `{userText, assistantText, ...}` 扩成 `{userText, blocks: AnthropicBlock[], ...}`
- streaming 中根据事件 append blocks(text 累加到末尾 text block;tool_use /
  tool_result 直接 push 新 block)
- 渲染逻辑复用现有 `MessageBlocks` / `ToolUseCard` / `ToolResultCard`(`Chat.tsx:727-774`)
- `loadConvDetail` 仍保留作 turn 结束后 reconciliation 安全网,但 UI 不再依赖
  它来"首次看到工具"

## 12. 表清单 / migration(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §9](history/0.4.0/DESIGN.md)。0.5.0 不加新表,不改 schema,
只加 advisory lock 的应用层逻辑。

## 13. 单例 / lifespan(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §10](history/0.4.0/DESIGN.md)。

## 14. AnthropicModel(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §11](history/0.4.0/DESIGN.md)。0.5.0 起 Agent 永远传
`stream=True`,所以 AnthropicModel 的非流式分支(`respond(stream=False)`)实际不
再被 Agent 调用,但保留以兼容 SDK 直接 caller。

## 15. 管理面(沿用 0.4.0,见归档)

[`history/0.4.0/DESIGN.md` §12](history/0.4.0/DESIGN.md)。

## 16. 设计模式 + 测试覆盖

沿用 0.4.0([`history/0.4.0/DESIGN.md` §15](history/0.4.0/DESIGN.md))。0.5.0 新增
覆盖:

- `tests/server/test_agent_streaming_loop.py`:mock model 给多轮 tool_use stream
  → 断 server 转出来的 SSE 序列(多 message_start) + DB 持久化时机 + max_iter +
  流中断恢复
- `tests/server/test_conversation_lock.py`:并发两个 client 打同 conv_id,断
  acquire / release 顺序 + 超时 503
- `tests/sdk/test_streams_events.py`:回放真实 SSE 字节流断 typed events 序列

## 17. 0.6.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.6.0**:Agent 自我进化循环(读 logs feedback 调权重 / 切 model / 修 prompt)
- **0.7.0**:多 Agent 实例(logs / conversations / tools 加 agent_id 维度)
