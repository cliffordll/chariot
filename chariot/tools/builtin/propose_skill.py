"""ProposeSkillTool —— agent 自发提议新 skill(B6 wave 3)。

封装策略(CLAUDE.md ⭐):
- 单类,持 `SkillProposeService | None` 引用(bootstrap 后注入)
- 所有链路逻辑收在 `SkillProposeService`,本类只负责 schema + input 校验 + dispatch
- 模块级零自由函数

安全前置(强制串完整 B5 链路):
1. **guardrail** —— 命中 `self_modify_chariot` 规则(`tool_name == "propose_skill"`),
   `enable_self_mod=False` → DENY;`enable_self_mod=True` → REQUIRE_APPROVAL
2. **ApprovalPolicy** —— `yolo=True` 放行 REQUIRE_APPROVAL,否则当 DENY
3. **CheckpointManager.create** —— 命中允许后,跑 `before-skill-propose-<name>`
4. **SkillRepo.create** —— enabled=False(人工 enable 才生效)
5. **audit** —— 全程写 `audit_events`(成功 status='create',失败 status='failed')

上面 1-2 由 `ToolExecutionService` 拦截层负责;3-5 由 `SkillProposeService` 编排;
本类只在 2 放行后调 service。

输入 schema(对应 `chariot/skills/builtin/*.yaml` 规约):
- name:`[a-z][a-z0-9_]*`,1-64 char
- description:8-200 char(YAML schema 是 1-240,这里更严避免空描述)
- prompt:16-4000 char(YAML schema 是 1-4000,16 是经验下限避免单字 prompt)
- allowed_tools / forbidden_tools / tags / version:可选
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool

if TYPE_CHECKING:
    from chariot.skills.propose_service import SkillProposeService


@builtin_tool(defaults={})
class ProposeSkillTool(BaseTool):
    """agent 自发提议新 skill 的 tool。

    bootstrap 注入路径::

        agent = AIAgent.bootstrap(db_path)
        # bootstrap 内部:
        #   service = SkillProposeService(sessionmaker=..., audit_hooks=..., checkpoint_manager=...)
        #   tools['propose_skill'].attach_service(service)
    """

    _DESCRIPTION: ClassVar[str] = (
        "Propose a new skill manifest to be stored (disabled by default) in chariot's "
        "skills table. Requires capability `enable_self_mod=true` and yolo (or human approval) "
        "to pass guardrails. Auto-creates a checkpoint before persisting. "
        "Use sparingly — proposed skills land disabled and need a human to enable them."
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self._service: SkillProposeService | None = None

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        # options 暂无;留扩展空间(per-tool quota 等未来再加)
        return cls(name=entry.name)

    def attach_service(self, service: SkillProposeService) -> None:
        """`AIAgent.bootstrap` 装完 tool 后调,注入 service 引用。

        约定:不挂 service 时 `execute` 直接返 is_error(测试 / 错配路径)。
        """
        self._service = service

    @property
    def service(self) -> SkillProposeService | None:
        return self._service

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "required": ["name", "description", "prompt"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9_]*$",
                        "maxLength": 64,
                        "description": "Skill identifier (lowercase, underscores, no spaces).",
                    },
                    "description": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 200,
                        "description": "One-line summary of what this skill does.",
                    },
                    "prompt": {
                        "type": "string",
                        "minLength": 16,
                        "maxLength": 4000,
                        "description": "The skill's system-prompt block content.",
                    },
                    "version": {
                        "type": "string",
                        "description": "Semver; defaults to 0.1.0.",
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "If set, restrict tool calls under this skill to these.",
                    },
                    "forbidden_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tools never allowed under this skill (overrides allowed_tools).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        if self._service is None:
            return self._error("propose_skill 未注入 service —— 检查 bootstrap 顺序")

        from chariot.skills.propose_service import ProposeInput

        # 浅校验 —— ToolExecutionService 已做 schema 校验(若 client 走流式 input);
        # 这里只兜底必填字段,深校验交给 SkillProposeService._build_yaml
        try:
            name = input["name"]
            description = input["description"]
            prompt = input["prompt"]
        except KeyError as exc:
            return self._error(f"input 缺必填字段: {exc}")
        if not isinstance(name, str) or not isinstance(description, str) or not isinstance(prompt, str):
            return self._error("name / description / prompt 必须是字符串")

        propose_input = ProposeInput(
            name=name,
            description=description,
            prompt=prompt,
            version=str(input.get("version") or "0.1.0"),
            allowed_tools=list(input["allowed_tools"]) if input.get("allowed_tools") is not None else None,
            forbidden_tools=list(input.get("forbidden_tools") or []),
            tags=list(input.get("tags") or []),
        )
        result = await self._service.propose(propose_input)
        if not result.ok:
            return self._error(result.error or "propose 失败(未知错误)")
        msg = (
            f"proposed skill {result.skill_name!r} (id={result.skill_id}); "
            f"checkpoint={result.checkpoint_id or 'skipped'}; "
            f"run `chariot skill enable {result.skill_name}` to activate"
        )
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": msg}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }
