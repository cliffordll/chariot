# Chariot 架构设计(0.7.0)

> **当前版本**:`0.7.0`(开发中)
> **上一版归档**:[`docs/history/0.6.5/DESIGN.md`](history/0.6.5/DESIGN.md)
>
> **0.7.0 主题**:OpenAIProvider + Memory + Skills(自演化基础)。详见
> [`ROADMAP.md`](ROADMAP.md) v2 章节;具体设计待 0.7.0 开发启动时落地。
>
> 0.6.5 落地内容(架构修正,绝不留技术债)冻结在
> [`docs/history/0.6.5/DESIGN.md`](history/0.6.5/DESIGN.md):
> - `BaseProvider` 不再持 httpx client → `ClientCache` + `ClientSpec` 进程级共享
> - `AIAgent` 撤单例 → `AgentRegistry` per-session 缓存(LRU 32)
> - `ChatRequest.model` per-call 字段加回 + per-call wire override 路径
> - 撤 `chariot/server/` 整目录 + fastapi/uvicorn 依赖;CLI 直接 in-process 调
>   AIAgent;Tauri 切到 stdio JSON-RPC sidecar
>
> 0.6.5 之前的设计参见各历史快照:`docs/history/<version>/DESIGN.md`。

---

(0.7.0 章节待补)
