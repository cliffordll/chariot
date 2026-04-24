"""批量模式执行器:脚本 / GUI 场景用,不依赖 terminal UI。

`ChatBatch` 持有 `ChatContext`,从一组 user text 顺序跑多轮。不打印 token / meta
到 terminal,结果以 `list[TurnResult]` 返回给调用方。

与 `ChatRepl` / `ChatOnce` 对称,三者都是"`ChatContext` 上的一种执行模式":
- `ChatRepl`:terminal 交互循环(依赖 `Renderer` + `input()`)
- `ChatOnce`:terminal 一次性请求(依赖 `Renderer`)
- `ChatBatch`:脚本 / GUI 批量跑(**不依赖 UI**,纯数据入/出)

典型 caller:未来的 GUI(Tauri webview 通过 invoke 喂一批输入)/ 集成测试 /
脚本化批量问答。

v0.1 语义
---------
- 单 `ctx` 累积多轮历史(跟 REPL 一致,只是不交互)
- 每轮失败撤回该 user 消息,跳过继续下一条(不中断整批)
- 返回值 `list[TurnResult]` 与成功轮数对齐;失败轮不占位

若要"每条独立问答 · 不累积",caller 每次传入新的 `ctx.reset()` 过的实例。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from chariot.cli.core.context import ChatContext, ChatError, TurnResult


@dataclass
class ChatBatch:
    """批量执行器:从迭代器取 user text,逐条发,收集 `TurnResult`。"""

    ctx: ChatContext
    # 每轮失败收集到的错误(status + short body),调用方可选择读
    errors: list[ChatError] = field(default_factory=lambda: [])

    async def run(self, texts: Iterable[str]) -> list[TurnResult]:
        """顺序跑 texts,成功的轮次追到 messages 并收集结果;失败撤回 user 跳过。"""
        results: list[TurnResult] = []
        for text in texts:
            self.ctx.append_user(text)
            try:
                # batch 不打印 token,on_token 回调空跑
                result = await self.ctx.run_turn(on_token=_noop)
            except ChatError as e:
                self.ctx.pop_last()
                self.errors.append(e)
                continue
            self.ctx.append_assistant(result.text)
            results.append(result)
        return results


def _noop(_: str) -> None:
    """on_token 占位;batch 不消费增量 token(只在收尾拿完整 TurnResult.text)。"""
