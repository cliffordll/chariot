"""Repository 层:封装 ORM 查询,agent / surface 按业务语义调用。

层次:
- `database/`:infra(engine / session / migrations / ORM 声明)
- `repos/`:data access(按表分类的 query helper)
- `agent/` / surface 层:调用 repo,不直接写 SQLAlchemy

错误语义:repo **不抛** HTTP-shaped 异常。语义错(如 name 冲突 / 记录不存在)
抛 `ConfigError` 子类(`DuplicateProviderName / ProviderNotFound`),原始 SQLAlchemy
异常透传给调用方。
"""

from __future__ import annotations

from chariot.repos.audit_repo import AuditEvent, AuditRepo
from chariot.repos.checkpoint_repo import CheckpointEntry, CheckpointRepo
from chariot.repos.conversation_repo import Conversation, ConversationRepo
from chariot.repos.eval_repo import EvalCaseEntry, EvalRepo, EvalRunEntry
from chariot.repos.memory_repo import MemoryEntry, MemoryRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.repos.skill_repo import SkillEntry, SkillRepo
from chariot.repos.log_repo import LogRepo
from chariot.repos.tool_repo import ToolRepo

__all__ = [
    "AuditEvent",
    "AuditRepo",
    "CheckpointEntry",
    "CheckpointRepo",
    "Conversation",
    "ConversationRepo",
    "EvalCaseEntry",
    "EvalRepo",
    "EvalRunEntry",
    "MemoryEntry",
    "MemoryRepo",
    "ProviderRepo",
    "SkillEntry",
    "SkillRepo",
    "LogRepo",
    "ToolRepo",
]
