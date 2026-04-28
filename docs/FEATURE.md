# Chariot 0.5.0 推进表

> **当前活跃**:`0.5.0`
> **上一版归档**:[`docs/history/0.4.0/FEATURE.md`](history/0.4.0/FEATURE.md)
>
> **0.5.0 主题**:**协议级流式工具循环**。撤掉 Agent slow path 的 `stream=False`
> 中间步 + `stream=True` 收尾重发,改成全程 streaming;一条 HTTP 响应里多个
> `message_start ... message_stop` 块串联,server 在轮间合成 `tool_result` 消息块。
> CLI / UI / 透传客户端共享一份原生 Anthropic 事件流。详细架构见 [`DESIGN.md`](DESIGN.md)。

每步推进规则(沿用):每完成一步 → 跑验证 → 等用户确认"通过"再标 ✅,然后 commit。
一个 FEATURE 步骤 = 一个 commit。

**执行顺序调整**(用户指令):S.3 推迟,实际推进顺序为 S.1 → S.2 → S.4 → S.3 → S.5。
S.x 标号不动(commit message 引用稳定),只是按文档章节顺序读时不等于推进顺序。

---

## 0.5.0 patch 列表

### S.1 ✅ server agent 流式工具循环

**目标**:`Agent._run_tool_loop` 全程 streaming;一条响应里串多个 `message_start
... message_stop` 块,server 合成 `tool_result` message。

- `chariot/server/agent.py`:
  - 撤掉当前 `model.respond(stream=False)` 的 while 循环 + 收敛后 `stream=True`
    重发(`agent.py:222-244` 段)
  - 新方法 `_stream_tool_loop(body_dict, model, conversation_id, conv_repo)` 返
    `AsyncIterator[bytes]`(SSE bytes 给 client),内部:
    - `await model.respond(body, stream=True)` 拿上游 SSE
    - 边转发 SSE 边 buffer assistant content blocks(text + tool_use)
    - `message_stop` 后:
      - 有 tool_use → 持久化 assistant content + 跑工具 + 合成 `tool_result`
        message SSE 帧推给 client + 持久化 `user`(tool_result)+ 拼下轮 body +
        续循环
      - 无 tool_use → 持久化 assistant content + 关流
  - 工具执行错误 → 合成 `tool_result is_error=true` message;不中断流
  - max_iter 超 → 写 Anthropic 协议原生 `event: error` 帧 + 关流
- `chariot/server/controller/dataplane.py`:`StreamingResponse` 直接消费上面
  的 `AsyncIterator[bytes]`,不再走"非流式拿全文 + 转 stream"的 fallback
- `chariot/server/agent.py` 持久化时机:每轮 `message_stop` 后立即 `append_message`
  (assistant content + 合成的 tool_result user content)
- 测试:
  - `tests/server/test_agent_streaming_loop.py`(新):
    - mock model 返第 1 轮 tool_use stream + 第 2 轮 text-only stream → 断
      server 转出的 SSE 序列含 2×`message_start` + 1× tool_result 合成块
    - max_iter 超 → 断 `event: error` 帧
    - 工具抛 → 断 `tool_result is_error=true`
    - 持久化时机:每个 message_stop 后 messages 表已有对应行
  - `tests/server/test_agent_tool_loop.py` 旧 case(0.4.0 非流式)→ 标 deprecated
    或重写到流式
- **验证**:`uv run pytest -q` + `uv run ruff check .` + `uv run pyright chariot/`

### S.2 ✅ SDK 流解析适配 ChatStream

**目标**:`ChatStream.events()` 吐 typed StreamEvent;`text_deltas` 保留兼容。

- `chariot/sdk/streams.py`:
  - 加 `StreamEvent` dataclass(`kind` + 各 kind 专属字段;DESIGN §10.1)
  - 加 `events(resp) -> AsyncIterator[StreamEvent]`,处理:
    - 多 `message_start` 累加 input/output tokens
    - `content_block_start(text)` + 累积 `text_delta` → 多个 `kind=text` event
    - `content_block_start(tool_use)` → `tool_use_start`;累积 `input_json_delta`
      → `tool_use_input` event(可选,部分 caller 不要 partial JSON);
      `content_block_stop` 后 parse JSON 完整 input → `tool_use_complete`
    - `content_block_start(tool_result)` + 累积内容 → `tool_result` event(server
      合成的 user message 里的 block)
    - 每个 `message_stop` → `turn_complete`(usage 给本轮统计)
    - 整个流结束 → `stream_done`
  - `text_deltas(resp)` 实现改成 `events(resp)` + 过滤 `kind=="text"` yield text
- 测试:
  - `tests/sdk/test_streams_events.py`(新):回放手写 SSE 字节序列(多
    message_start + tool_use + tool_result + text)→ 断 events 序列
  - `tests/sdk/test_streams.py` 旧 `text_deltas` case 全部仍通过(回归)
- **验证**:`uv run pytest -q tests/sdk/`

### S.3 跨客户端协调 + CLI fetch_history + advisory lock

**目标**:解决 issue 2(CLI 不知 UI 写入)和 issue 3(并发写时 model 看到错乱)。

- **server-side advisory lock**:
  - `chariot/server/repository/conversation_repo.py` 加 `acquire_lock(conv_id, timeout_s) -> AsyncContextManager`,实现用 SQLite `BEGIN IMMEDIATE` 长事务包住
    `load → append user → call model → append assistant`
  - `chariot/server/agent.py` `_stream_tool_loop` 起步段用 `async with conv_repo.acquire_lock(conversation_id):` 包整段循环
  - 等待超时(env `CHARIOT_CONV_LOCK_TIMEOUT_S`,默认 30s)→ 503 +
    `code: conversation_busy`
- **SDK fetch history**:
  - `chariot/sdk/proxy_client.py` 已有 `get_conversation()` 拉 conv 元;**确认**
    或新增 `get_conversation_messages(id) -> list[anthropic_msg]` 拉 canonical
    history(若已存在,跳过)
- **CLI auto-refresh**:
  - `chariot/cli/core/context.py` 加 `refresh_from_server() -> None` 方法,
    stateful only;调 SDK 拉 history 替换 `self.messages`
  - `ChatRepl._one_turn` send 前自动调一次(env `CHARIOT_CLI_AUTO_REFRESH=0`
    可关闭)
- 测试:
  - `tests/server/test_conversation_lock.py`(新):并发两 client 同 conv_id,
    断 acquire / release 顺序 + 超时 503 + lock 释放后第二个 client 看到
    第一个写入的内容
  - `tests/cli/test_context_refresh.py`(新):mock SDK 返 server canonical
    messages,断 `refresh_from_server` 替换本地 `self.messages`
- **验证**:`uv run pytest -q`

### S.4 ✅ CLI REPL 渲染流式工具

**目标**:dim 灰行实时打印 tool_use / tool_result。

- `chariot/cli/core/render.py`:加 `tool_use_line(name, input_repr)` /
  `tool_result_line(text, is_error)`(dim 灰 / 红前缀)
- `chariot/cli/core/context.py`:
  - 改 `run_turn` 签名:不再单回调 `on_token`,改成多回调或 yield events:
    - 简洁版:加 `events()` AsyncIterator 直接吐 SDK StreamEvent
    - REPL caller `_one_turn` async for event:dispatch 到 Renderer
- `chariot/cli/core/repl.py` `_one_turn` 改写消费 events
- `chariot/cli/core/once.py` / `batch.py` 同步:吃 events,但只关心 text + meta
- 测试:
  - `tests/cli/test_repl_tool_render.py`(新):mock SDK events → 断 Renderer
    收到的调用序列(text 走 stream_token,tool_use 走 tool_use_line,etc.)
- **验证**:`uv run pytest -q tests/cli/` + 手测 REPL(stateful conv,触发工具)

### S.5 UI Chat 页实时渲染 tool blocks + docs + 版本号

**目标**:pending 卡片渐进 append tool_use / tool_result blocks,撤掉"等
loadConvDetail 才看到工具"的依赖。

- `packages/app/src/lib/streams.ts`:
  - 加 typed event handlers(同 SDK ChatStream typed events)
  - 流式回调 `onEvent(event: StreamEvent)`
- `packages/app/src/pages/Chat.tsx`:
  - `pending` 类型从 `{userText, assistantText, ...}` 改成 `{userText, blocks: AnthropicBlock[], status, meta, ...}`
  - `runTurn` 调用 `onEvent`:text 累加进末尾 text block;tool_use_complete 推
    新 tool_use block;tool_result 推 tool_result block
  - 渲染:复用 `Chat.tsx:727-774` 的 tool_use / tool_result 卡片
  - `loadConvDetail` 保留作 reconciliation,turn done 后调一次(server canonical
    校对)
- `docs/DESIGN.md` 实施过程偏差校正(若有)
- `docs/FEATURE.md` 全部步骤标 ✅
- `docs/ROADMAP.md` 更新:0.5.0 协议级流式标已完成,0.6.0 / 0.7.0 上调到 0.5.0+ /
  0.6.0+ 表述
- `README.md`:CLI / UI 流式工具 demo 段(可选截图)
- 版本号 `pyproject.toml` + `chariot/__init__.py`:`0.4.4 → 0.5.0`
- **验证**:全套(ruff / format / pyright / pytest / bun build)+ 手测端到端
  (CLI REPL 看到 → read_file 行;UI Chat 卡片实时 append tool 卡片)

---

## 0.6.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.6.0**:Agent 自我进化循环(读 logs feedback 调权重 / 切 model / 修 prompt)
- **0.7.0**:多 Agent 实例(logs / conversations / tools 加 agent_id 维度)
