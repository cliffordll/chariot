# B3 — Memory & Context 升级

> Milestone:`feat/0.8.2-memory-context`
> 范围:补 phalanx §2.8.b 的 3 子能力 — FTS5 全文搜索 / Context 自动压缩 / `@reference` 解析
> 总规划上下文:`docs/evolution-design.md` §6.3

## Wave 拆分

| Wave | 内容 |
|---|---|
| 1 | FTS5:v18 migration `messages_fts` virtual table + `ConversationRepo` app-level sync + `search()` + `rebuild_fts()`;sidecar `search_conversation` RPC;CLI `chariot conversation search "..." [--conversation X]`;Tests |
| 2 | Context 自动压缩:`AuxiliaryClient`(副 model 路由)+ `ContextCompressor`(prompt_tokens > context_length × 0.7 时摘要最早 N turn 为 `[context-summary]`,fallback oldest-pair pruning);wire 进 AgentLoop;Tests |
| 3 | `@reference` 解析:user 消息预处理 hook + 4 种解析器(`@file:` / `@diff[:ref]` / `@url:` / `@session:<id\|prefix>`)→ 展开为 `<reference type=... key=...>` 块;Tests |
| 4 | 桌面 Conversations 页(全局搜索 + 单 conv 内搜索)+ demo 文档收尾 |

---

## Wave 1 详细设计:FTS5

### Schema(v18 migration)

`messages_fts` 用 SQLite FTS5 standalone(非 contentless-external),3 列 UNINDEXED
+ 1 列 indexed(`content`)。**不用 trigger sync** —— 现有 `_split_sql_statements`
按 `;` 切 SQL,trigger 体内的 `;` 会被错切;改纯 Python 同步,顺手还能从
JSON content 抽 text-only,避免 'type' / 'tool_use' 这种 anthropic schema
关键字成噪声词。

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    role UNINDEXED,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);
-- 回填:从 messages 全表导入(初版直接索引 JSON 原文,后续 rebuild_fts 切到 text-only)
```

`unicode61` tokenizer + `remove_diacritics=2`:中英文混查较稳;不开 `porter`
(中文用不上 stemming,英文 porter 在 chariot 语料量下噪声大)。

### App-level sync(`ConversationRepo`)

- `append_message`:`session.flush()` 拿 `msg.id` 后,`INSERT INTO messages_fts`
  用 `_extract_indexable_text(content)`(从 anthropic blocks 抽 text /
  tool_use.input JSON / tool_result.content,跳过 schema 字段)
- `delete(conversation_id)`:`DELETE FROM messages_fts WHERE conversation_id = :cid`
- `rebuild_fts()`:灾备命令,清空 + 全量回填;调用方:`chariot conversation rebuild-fts`

### 搜索 API

```python
@dataclass(frozen=True)
class MessageSearchHit:
    message_id: str
    conversation_id: str
    role: str
    snippet: str   # FTS5 snippet() 包 <mark>...</mark>
    rank: float    # bm25(越小越好)

async def search(
    self,
    query: str,
    *,
    limit: int = 20,
    conversation_id: str | None = None,
) -> list[MessageSearchHit]: ...
```

- `query` 透传 FTS5 MATCH 表达式;最简形式空格分隔多词 ≈ AND
- 默认 bm25 排序 + top-N
- 给 `conversation_id` 时只查该会话内(给 stateful chat 内部找上下文用)

### Surface

- sidecar RPC `search_conversation(query, limit?, conversation_id?)` → `{ hits: MessageSearchHit[] }`
- CLI `chariot conversation search "<query>" [--limit N] [--conversation X]`
- CLI `chariot conversation rebuild-fts`(灾备)
- 桌面端 wave 4 加,见 wave 4 节

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.6。

---

## Wave 2 详细设计:Context 自动压缩

### 问题

长对话越跑 prompt 越大,撞 context window 上限 → provider 拒收。要在送给
provider 之前判断 token 余量,过 threshold 自动把"最早 N turn"摘要成一句话,
腾出 context。

### Schema(v19 migration)

```sql
CREATE TABLE auxiliary_clients (
    name TEXT PRIMARY KEY,            -- 'summarizer' / 'critic_aux' 等
    provider_entry TEXT NOT NULL,     -- 指向 providers.name(独立路由 + 独立 budget)
    model TEXT,
    params TEXT NOT NULL DEFAULT '{}',-- max_tokens / temperature / 等
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- seed:插一条 default summarizer 指向 mock provider
```

`AuxiliaryClient` 不引入新 `BaseProvider` 子类 —— 它是"指向某个现有 provider
entry 但用独立 params"的 wrapper,内部仍走 `ProviderRegistry`。独立 budget
防止"摘要本身把主任务的 token 预算吃光"。

### Token 估算

不引入 tiktoken(额外 dep + Anthropic 跟 OpenAI tokenization 不同步)。
用粗估:

```python
token_est = sum(len(text) // 3 for text in messages_text)
```

中文 ~3 char/token,英文 ~4 char/token,折中。准确度差 ~30%,但够触发判断;
后续可接 provider-specific token counter API。

### `ContextCompressor` 类

```python
class ContextCompressor:
    def __init__(
        self,
        aux_client: AuxiliaryClient,
        *,
        threshold: float = 0.7,
        summary_turns: int = 5,
    ) -> None: ...

    async def maybe_compress(
        self,
        messages: list[Message],
        *,
        context_length: int,  # 从 ProviderEntry.params.context_length 取,默认 8192
    ) -> list[Message]:
        """估 prompt_tokens / context_length;过 threshold 时:
        1. 抓最早 summary_turns 个 turn(从 messages[1:] 起,保留 system 头)
        2. 调 aux_client 摘要 → '[context-summary] <text>'
        3. 替换:返 [system] + [summary_msg] + 剩余 turn
        摘要失败 → fallback oldest-pair pruning(直接丢最早 2 条 user/assistant)。
        """
```

### 接入点

`AgentLoop.stream_chat`,在 `provider.generate(current_req)` 之前过一遍。
压缩事件写 `audit_events` 表 + B1 trace `trace_turns.meta` 加
`context_compressed: true` 标记,后续 audit / RL 数据流要用。

### Surface

- CLI `chariot auxiliary {list, add, update, remove}` 管 `auxiliary_clients` 表
- sidecar `list_auxiliary_clients` / `create_auxiliary_client` / etc RPC
- 桌面端 wave 4 暂不加专属页;可在 Providers 页底加 "Auxiliary clients" 区块

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.7。

---

## Wave 3 详细设计:`@reference` 解析

### 用户场景

```
user: @file:README.md 给我一个一句话总结
user: @diff:HEAD~3 这三个 commit 改了什么?
user: @url:https://example.com 这个站点是干啥的
user: @session:01HABCDE 接着上次 trace 那个 conversation 的思路继续
```

### 展开格式

```xml
<reference type="file" key="README.md">
<file 内容>
</reference>
```

错误时:

```xml
<reference type="file" key="ghost.md" error="not found"></reference>
```

保留 reference 标记给 agent 知道用户原本想引用什么,即使内容拿不到。

### 类层级

```python
class BaseReferenceResolver(ABC):
    type_id: ClassVar[str]
    @abstractmethod
    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference: ...

class FileReferenceResolver(BaseReferenceResolver):    type_id = "file"
class DiffReferenceResolver(BaseReferenceResolver):    type_id = "diff"
class UrlReferenceResolver(BaseReferenceResolver):     type_id = "url"
class SessionReferenceResolver(BaseReferenceResolver): type_id = "session"

REFERENCE_RESOLVERS: dict[str, type[BaseReferenceResolver]] = {
    "file": FileReferenceResolver,
    "diff": DiffReferenceResolver,
    "url": UrlReferenceResolver,
    "session": SessionReferenceResolver,
}
```

### 接入点

`AgentLoop` 在收到 user message content 后、送给 provider 之前调
`ReferenceExpander.expand(messages)`。正则扫
`@(file|diff|url|session):<key>` 模式,异步并发 resolve(`asyncio.gather`),
拼回。

### Security 红线

- `@file:` 限制在 cwd 子树下(防遍历 `../../`);失败 → error reference
- `@url:` 走现有 `http_get` 工具的 allowlist(继承同套黑名单);timeout 默认 10s
- `@diff:` 在 cwd 跑 `git diff`,subprocess 限超时 + 输出截断
- `@session:` 仅当前 user 的会话可见(目前 chariot 单用户;多租户上线后强制 owner 过滤)

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.8。

---

## Wave 4 详细设计:桌面 Conversations 页 + 收尾

### 桌面端

- `packages/app/src/pages/Conversations.tsx`(新):全局对话列表 + 顶部
  搜索框 → 调 `search_conversation`;hit 卡片点击跳到对应 conversation 详情
- `packages/app/src/pages/Conversation.tsx`(新或并入):单 conversation 视图
  内加搜索,hit 高亮跳转到对应 message
- `packages/app/src/routes.tsx` 加 `/conversations` + `/conversations/:id`

### Demo 文档收尾

- 校准 `docs/guides/agent-binding-demo.md` §7.6 - §7.9 所有命令 vs 最终 CLI 一致
- 给 wave 2 / 3 补 trace view 截图(若可行)+ 桌面端截图

### 验收

- `chariot conversation search "..."` 跑出 hit 列表
- 长对话(20+ turn)触发 context 压缩,`chariot trace view <turn>` 看
  `meta.context_compressed=true`
- `chariot chat "@file:README.md ..."` 工作,展开后 agent 能引用内容
- 桌面 Conversations 页搜索框工作,点击 hit 跳转
- 全 pytest / ruff / pyright / bun build 通过
