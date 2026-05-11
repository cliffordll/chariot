"""SecretScrubber —— 敏感串扫除(B7 wave 1)。

封装策略(CLAUDE.md ⭐):
- 单类编排;模块级零自由函数 / 零模式集合(全挂 ClassVar)
- `scrub(text)` 字符串 → 字符串(同长度;只替换不删)
- `scrub_blocks(blocks)` 走 Anthropic blocks list,递归 text 字段;非 text
  类型(tool_use input / tool_result content 等)走 `scrub_value` 兜底

保守策略:正则集合只匹配明显模式,false positive 比 false negative 危害大。
`--raw` 用 `NullScrubber`(no-op)兜底。
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

_REDACTED = "<REDACTED>"


class SecretScrubber:
    """敏感串扫除。

    用法::

        scrubber = SecretScrubber()
        clean = scrubber.scrub("export SK=sk-abcdef1234567890abcdef")  # → "export SK=<REDACTED>"
        clean_blocks = scrubber.scrub_blocks(req.messages[0].content)

    模式集合(ClassVar,跨实例共享):
    - api_key:`sk-...` / `xoxb-...` / `pk_...`
    - aws_access:`AKIA[A-Z0-9]{16}`
    - bearer:`bearer <token>`
    - private_key:PEM block 标记
    """

    PATTERNS: ClassVar[list[tuple[str, re.Pattern[str]]]] = [
        ("api_key_sk", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
        ("api_key_xoxb", re.compile(r"xoxb-[A-Za-z0-9\-]{20,}")),
        ("api_key_pk", re.compile(r"pk_(live|test)_[A-Za-z0-9]{20,}")),
        ("aws_access", re.compile(r"AKIA[A-Z0-9]{16}")),
        ("aws_secret", re.compile(r"(?i)aws_secret[_ ]?(?:access_)?key.{0,5}[=:][\"' ]?[A-Za-z0-9/+=]{30,}")),
        ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}")),
        ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
        ("github_pat", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ]

    def scrub(self, text: str) -> str:
        """跑全部 pattern;命中替换为 `<REDACTED>`(保留前缀以便审阅)。"""
        if not isinstance(text, str) or not text:
            return text
        out = text
        for _name, pattern in self.PATTERNS:
            out = pattern.sub(_REDACTED, out)
        return out

    def scrub_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic content blocks 数组(text / tool_use / tool_result)递归扫除。

        - `text` block 的 `text` 字段走 `scrub`
        - `tool_use` 的 `input` 走 `scrub_value`(递归 dict / list)
        - `tool_result` 的 `content`(字符串或 nested blocks)递归
        - 其它未知 type:整 block 走 `scrub_value`
        """
        out: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                out.append(self.scrub_value(block))  # type: ignore[arg-type]
                continue
            block_type = block.get("type")
            new_block = dict(block)
            if block_type == "text":
                new_block["text"] = self.scrub(str(block.get("text", "")))
            elif block_type == "tool_use":
                new_block["input"] = self.scrub_value(block.get("input"))
            elif block_type == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    new_block["content"] = self.scrub(content)
                elif isinstance(content, list):
                    new_block["content"] = self.scrub_blocks(content)
                else:
                    new_block["content"] = self.scrub_value(content)
            else:
                # 未知 type:整 block 兜底
                new_block = {k: self.scrub_value(v) for k, v in new_block.items()}
            out.append(new_block)
        return out

    def scrub_value(self, value: Any) -> Any:
        """通用递归 scrub —— 走 str / dict / list / tuple,其它类型(int/bool/None)透传。"""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {k: self.scrub_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.scrub_value(v) for v in value)
        return value


class NullScrubber(SecretScrubber):
    """`--raw` 模式 —— 不替换任何内容。"""

    def scrub(self, text: str) -> str:
        return text

    def scrub_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(blocks)

    def scrub_value(self, value: Any) -> Any:
        return value
