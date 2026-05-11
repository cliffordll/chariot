# B4 — Reflection & Critic

> Milestone:`feat/0.8.3-reflection`
> 范围:补 phalanx §2.8.c 的 3 子能力 — Critic role / Reflect-then-retry / VERDICT 强制契约
> 总规划上下文:`docs/evolution-design.md` §6.4
> 设计前提:复用 B3 wave 2 已落地的 `AuxiliaryClient`(副 model 路由)和
> `auxiliary_clients` 表;新增 `name='critic'` row 即可装一个独立 budget 的 critic LLM。
> 模块归属:**不开新顶层目录**(evolution-design.md §6.4 第 5 条);全部落进
> `chariot/agent/reflection/` 子包(reflection 是 AIAgent 行为)。

## Wave 拆分

| Wave | 内容 |
|---|---|
| 1 | Critic 基建:`CriticVerdict` 数据对象 + `CriticAgent` 类(复用 AuxiliaryClient,加强制 `VERDICT: PASS\|FAIL\|UNSURE` 一行 + reason 的 parser);bootstrap 走 `auxiliary_clients.name='critic'`;CLI `chariot critic try`;sidecar `critique_text` RPC;tests |
| 2 | Reflect-then-retry hook:`ReflectionLoop` 类挂在 `AgentLoop` 外层,工具失败 / verifier-style 失败 → 调 critic → critique 注入 user 消息 → 重跑;`reflect_max_retries` 预算 + circuit breaker;trace meta 写 verdict;tests |
| 3 | Agent profile 集成 + 桌面 UI:`agent_profiles` 加 `reflection_enabled` / `reflection_max_retries` 字段(v20 migration);AgentService / sidecar / Agents 页编辑 UI;Traces 详情页内嵌 critic verdict 列;demo 文档收尾 |

> 注:对比 B3 4-wave,本次更紧凑(3 wave)—— Critic / Reflection 本质都是
> "agent 内部行为",surface 主要靠 trace meta 暴露,不需要单独大页面。

---

## Wave 1 详细设计:Critic 基建

### CriticVerdict / Critic prompt 契约

```python
@dataclass(frozen=True)
class CriticVerdict:
    verdict: Literal["PASS", "FAIL", "UNSURE"]
    reason: str
    raw: str          # 原始 LLM 输出(给 trace 留存证据)
```

**Critic prompt 规范**(`CriticAgent.SYSTEM_PROMPT`):

```
你是任务评审 critic。读完下面的"任务上下文"+"产出",回答**严格两段**:

第一行必须形如:
VERDICT: PASS|FAIL|UNSURE
其它内容(reason / 改进建议)放第二段。

VERDICT 含义:
- PASS:产出满足任务要求,可交付
- FAIL:有明确缺陷,需重做(reason 必须给出具体偏差)
- UNSURE:信息不足以裁决(reason 说明缺什么信息)

只输出这两段,不要 preamble。
```

**Parser**:正则 `^VERDICT:\s*(PASS|FAIL|UNSURE)\s*$` 在首行;parse 失败 →
`verdict=UNSURE`,reason 写 `"critic 输出不符合 VERDICT 契约:<raw 前 200 char>"`。
这样 critic 即使"不听话"也不阻断主链路(降级为不信任此次裁决)。

### `CriticAgent` 类

```python
class CriticAgent:
    def __init__(self, aux: AuxiliaryClient) -> None: ...

    @classmethod
    def from_auxiliary_clients(
        cls,
        aux_entries: list[AuxiliaryClientEntry],
        providers: dict[str, BaseProvider],
    ) -> CriticAgent | None:
        """从 auxiliary_clients 表找 name='critic' 的行,装好返回;
        找不到 / dangling provider_entry 返 None(AIAgent 跳过 critic 装载)。"""

    async def critique(
        self,
        *,
        task_goal: str,
        produced: str,
        extra_context: str | None = None,
    ) -> CriticVerdict:
        """跑一次 critic LLM,parse 出 VERDICT + reason。"""
```

**为什么不直接复用 `AuxiliaryClient.summarize`**:summarize 不带 VERDICT 强契约
prompt;critic 需要约束 LLM 输出格式 + parse 出结构化 verdict。`CriticAgent`
是 `AuxiliaryClient` 的特化壳,而不是替代品。

### Seed / bootstrap

`auxiliary_clients` 表 B3 wave 2 已建;B4 不加新 migration,只在
`AuxiliaryRepo.seed_if_empty`(若未来加)或文档里推荐用户:

```bash
chariot auxiliary add --name critic --provider claude --model claude-haiku-4-5-20251001 \
    --param max_tokens=512 --param temperature=0.2
```

`AIAgent.bootstrap`:扫 `aux_entries`,找到 `name='critic'` 行 → 走
`CriticAgent.from_auxiliary_clients` 装上;找不到则 `self._critic_agent = None`,
所有 reflection 路径退化 noop。

### Surface(wave 1 最小)

- CLI `chariot critic try "<任务描述>" --produced "<产出>"`:手动跑一次 critique,
  打印 verdict + reason(给用户调试 critic prompt / 阈值用)
- sidecar `critique_text` RPC:`{task_goal, produced, extra_context?}` → `CriticVerdict`
- 桌面端 wave 3 再加

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.10。

---

## Wave 2 详细设计:Reflect-then-retry

### 触发条件

主 agent 跑完一轮(stream_done)或一轮内拿到工具结果之后,满足下面任一 → 进入
reflection:

1. **Tool failure** — 本轮某个 tool_result 的 `is_error=True`
2. **Agent self-report fail** — 主 agent 输出里含 `FAILED: ...` 模式(自报失败)
3. **External verifier fail** — B2 eval pipeline 跑 verifier 返 fail(B4 wave 2
   暂不接,留 hook;手动可通过 `force_reflect=True` 参数触发)

### `ReflectionLoop` 类

```python
class ReflectionLoop:
    def __init__(
        self,
        *,
        critic: CriticAgent,
        max_retries: int = 2,
        circuit_breaker_consecutive_fail: int = 3,
    ) -> None: ...

    async def maybe_reflect(
        self,
        *,
        agent_loop_result: AgentLoopOutcome,
        original_req: ChatRequest,
    ) -> ReflectionOutcome:
        """检查是否触发;触发 → 调 critic → 构造 critique-injected req;
        return ReflectionOutcome(should_retry, revised_req, verdict)。"""
```

`ReflectionOutcome`:

```python
@dataclass(frozen=True)
class ReflectionOutcome:
    should_retry: bool
    revised_req: ChatRequest | None   # should_retry=True 时非空
    verdict: CriticVerdict | None
    reason: str                        # noop / triggered / circuit_open / budget_exhausted
```

**Circuit breaker**:连续 N 轮拿到 FAIL verdict 但 retry 后还是 fail → 跳出 reflection
(防止"反思死循环"耗 token 预算)。N 默认 3,可调。

### Critique 注入格式

发现 fail → critic 给出 `verdict=FAIL, reason="缺 X / 错 Y"` → 把它打包成一条
user 消息塞到原 messages 末尾,作为新一轮的 input:

```
[REFLECTION]
critic verdict: FAIL
critic reason: 缺 X / 错 Y

请基于上面的反馈重新尝试该任务。
```

注:**不替换原 messages,只 append**;让主 agent 在 conversation history 里
能看到自己之前的输出 + critic 的反馈,形成连续推理。

### 接入点

`AIAgent.run_chat` 在 `loop.stream_chat(req)` 收完后,如果触发条件命中且有
`self._reflection_loop`,跑 `maybe_reflect` → 若 `should_retry=True`,继续
`loop.stream_chat(revised_req)`,直到 retry 用完或 critic 给 PASS。

每次 retry 写一条 `trace_turns.meta.reflection`:

```json
{
  "iteration": 1,
  "verdict": "FAIL",
  "reason": "...",
  "retry_count": 1,
  "max_retries": 2
}
```

### Surface(wave 2)

- CLI `chariot reflect run "<task>" --max-retries 3`:跑一个带 reflection 的 chat,
  详细打印每次 retry 的 verdict
- sidecar `run_chat_with_reflection` RPC(或在 `chat` 方法上加 `reflection_enabled`
  参数)
- 桌面端 wave 3 把 verdict / retry count 显示在 Traces 详情页

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.11。

---

## Wave 3 详细设计:Agent profile 集成 + 桌面 UI + 收尾

### v20 migration

`agent_profiles` 加两个 nullable 字段:

```sql
ALTER TABLE agent_profiles ADD COLUMN reflection_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_profiles ADD COLUMN reflection_max_retries INTEGER NOT NULL DEFAULT 2;
PRAGMA user_version = 20;
```

`AgentService` / sidecar `create_agent` / `update_agent` 透传字段;CLI
`chariot agent edit <name> --reflection-on` / `--reflection-off` / `--reflect-retries N`。

**默认 reflection_enabled=0**:即使 critic 装好了,默认也不强制每次 chat
都走 reflection — 由 agent_profile 显式开关。run_chat 路径:
- `req.agent_profile=None` 或 `profile.reflection_enabled=0` → 不进 reflection
- `profile.reflection_enabled=1` 且 `self._reflection_loop != None` → 进 reflection

### 桌面 UI

**Agents 页**:加 reflection 复选框 + retries 输入框,绑到 update_agent。

**Traces 详情页**:trace_turns.meta 含 `reflection` 字段时,在 turn 详情下方插
"Reflection" panel,展示每次 retry 的 verdict / reason / retry_count。

### 验收

- `chariot auxiliary add --name critic ...` + `chariot critic try` 跑通,verdict 解析正确
- agent_profile reflection_enabled=1 时,chat 长任务故意 fail 一次,trace meta
  里能看到 retry 记录;PASS 后停止
- 全 pytest / ruff / pyright / bun build 通过

### 测试 demo

见 `docs/guides/agent-binding-demo.md` §7.12。

---

## 风险 / 决策

- **Critic 不输出 VERDICT 怎么办?** → parser 兜底 UNSURE,trace 记 raw 让人复盘;
  长期靠 critic prompt 调优 + (可选)用 anthropic `tool_choice` 强约束输出
- **Retry 死循环?** → max_retries(默认 2)+ circuit breaker(连续 3 次 FAIL 退出)双层防护
- **Critic 自己也错怎么办?** → 这是 B4 不解决的问题(meta-critic 是 B7 RL 范畴);
  当前只做"critic 输出一致即采纳",不二次裁决
- **Token 预算?** → critic 走独立 AuxiliaryClient(B3 wave 2 已有路径);params
  独立(默认 max_tokens=512),不吃主任务预算
- **跟 B5 guardrails 的关系**?→ 没冲突。Reflection 是"输出质量回路",guardrails
  是"危险动作回路";B5 先于 reflection 跑(危险动作直接 DENY 不进 reflection)
