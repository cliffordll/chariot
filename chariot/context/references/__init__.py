"""@reference 解析框架(B3 wave 3)。

`BaseReferenceResolver` ABC + 4 个内建 resolver(file / diff / url / session)
+ `REFERENCE_RESOLVERS` registry + `ReferenceExpander`。

封装策略(CLAUDE.md ⭐):
- ABC + 子类 dispatch,不写 if/elif 三分支
- 每个 resolver 自带 `type_id` ClassVar 和 security 校验
- `ReferenceExpander` 是无状态工具类,实例化时绑 cwd / allowed_domains / sessionmaker
"""

from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)
from chariot.context.references.registry import REFERENCE_RESOLVERS, ReferenceExpander

__all__ = [
    "REFERENCE_RESOLVERS",
    "BaseReferenceResolver",
    "ReferenceExpander",
    "ReferenceResolveError",
    "ResolveContext",
    "ResolvedReference",
]
