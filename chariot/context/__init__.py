"""Context platform —— 三套截然不同但都围绕 "context" 的能力。

- `composer.py` / `ContextComposer`:**被动 snapshot**。把当前 `ChatRequest` +
  运行时状态 + memory entries 序列化成 `ContextSnapshot`(7-slice 数据对象),
  落 `context_snapshots` 表 / `context_traces` 给 B1 trace / audit / 后续 RL
  回放。**不改 request**。
- `compressor.py` / `ContextCompressor`(B3 wave 2):**主动 rewrite**。在送给
  provider 之前估 prompt token,过阈值就调 `AuxiliaryClient` 把"最早 N turn"
  摘要成 `[context-summary] ...`,腾出 context;摘要失败 fallback oldest-pair
  pruning。
- `references/` 子包(B3 wave 3):**user 输入解析**。把 user message 里的
  `@file:` / `@diff:` / `@url:` / `@session:` 展开成
  `<reference type=X key=Y>...</reference>` 块,给 agent 看的是包好内容的消息。

类比:Composer 是录像机(记录现状),Compressor 是 garbage collector(修剪 budget),
ReferenceExpander 是变量替换(把符号引用变成内容)。
"""

from chariot.context.composer import ContextComposer
from chariot.context.compressor import CompressionResult, ContextCompressor
from chariot.models.context import ContextSlice, ContextSnapshot

__all__ = [
    "CompressionResult",
    "ContextComposer",
    "ContextCompressor",
    "ContextSlice",
    "ContextSnapshot",
]
