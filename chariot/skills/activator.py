"""SkillActivator —— 把 active skill 拼进 ChatRequest(B6 wave 2)。

封装策略(CLAUDE.md ⭐):
- 单一类 + 静态方法;模块级零自由函数
- 无副作用;`activate` 返新 ChatRequest(`dataclasses.replace`)
- 不持状态;`SkillRegistry` 由 AIAgent 在 `_prepare_request` 阶段查好 skill 后
  把具体 `BaseSkill` 实例传进来,activator 只负责拼接

注入策略:
- `<skill name="...">{prompt}</skill>` 块**追加**在已有 `system` 文本之后
  (先 prompt bundle 再 skill,让 skill 是"最后一层提示")
- tool 过滤:`ChatRequest.tools` 已经被 `_inject_default_tools` / agent_profile.
  toolset_id 处理过 → activator 再过一次 skill.tool_filter,**取交集 + 减
  forbidden**(两层过滤独立,manifest.forbidden_tools 永远剔除)
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from chariot.agent.chat_request import SystemBlock

if TYPE_CHECKING:
    from chariot.agent.chat_request import ChatRequest, ToolSchema
    from chariot.skills.base import BaseSkill


class SkillActivator:
    """skill → ChatRequest 注入器。

    用法::

        skill = registry.get(req.skill or profile.default_skill or "")
        if skill is not None and skill.enabled:
            req = SkillActivator.activate(req, skill)
    """

    @staticmethod
    def activate(req: ChatRequest, skill: BaseSkill) -> ChatRequest:
        """把 skill 注入到 req:`<skill>` 块拼进 `system` + tool 列表按 manifest 过滤。

        - `req.system` 是 str → 追加;是 None → 直接用 skill 块
        - `req.system` 是 list[block](Anthropic system 块形式)→ 追加一条 text
          block(罕见路径,Anthropic API 1:1 形态)
        - `req.tools` 是 None → 不动(`_inject_default_tools` 还没跑到这里;
          activator 在 normalize 之前 / 之后 跑由 `_prepare_request` 决定)
        - `req.tools` 是 list → 应用 skill.tool_filter
        """
        new_system = SkillActivator._inject_system(req.system, skill)
        new_tools = SkillActivator._filter_tools(req.tools, skill)
        return dataclasses.replace(req, system=new_system, tools=new_tools)

    @staticmethod
    def _inject_system(
        existing: str | list[SystemBlock] | None,
        skill: BaseSkill,
    ) -> str | list[SystemBlock]:
        block = skill.prompt_block()
        if existing is None or (isinstance(existing, str) and not existing):
            return block
        if isinstance(existing, str):
            return existing.rstrip() + "\n\n" + block
        # Anthropic system 块 list 形态:追加一条 text block
        return [*existing, SystemBlock(type="text", text=block)]

    @staticmethod
    def _filter_tools(
        tools: list[ToolSchema] | None,
        skill: BaseSkill,
    ) -> list[ToolSchema] | None:
        if tools is None:
            return None
        filter_ = skill.tool_filter()
        # 用名字过滤后,按原顺序取存活的 ToolSchema 对象
        allowed_names = set(filter_.apply([t.name for t in tools]))
        return [t for t in tools if t.name in allowed_names]
