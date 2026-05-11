"""B5 wave 1 内置规则集合(13 条)。

每条规则一个文件,便于阅读 + 单测;`DEFAULT_RULES` 是按危险度 + 触发频率
排序的列表(`GuardrailEngine.with_defaults` 一次装上)。
"""

from __future__ import annotations

from chariot.guardrails.base import BaseRule
from chariot.guardrails.builtin.db_drop_table import DbDropTableRule
from chariot.guardrails.builtin.db_truncate_table import DbTruncateTableRule
from chariot.guardrails.builtin.file_write_outside_cwd import FileWriteOutsideCwdRule
from chariot.guardrails.builtin.file_write_secrets import FileWriteSecretsRule
from chariot.guardrails.builtin.http_post_unsafe import HttpPostUnsafeRule
from chariot.guardrails.builtin.network_exfil import NetworkExfilRule
from chariot.guardrails.builtin.self_modify_chariot import SelfModifyChariotRule
from chariot.guardrails.builtin.shell_chmod_unsafe import ShellChmodUnsafeRule
from chariot.guardrails.builtin.shell_curl_pipe_sh import ShellCurlPipeShRule
from chariot.guardrails.builtin.shell_dd_block_device import ShellDdBlockDeviceRule
from chariot.guardrails.builtin.shell_format_disk import ShellFormatDiskRule
from chariot.guardrails.builtin.shell_git_push_force import ShellGitPushForceRule
from chariot.guardrails.builtin.shell_rm_rf import ShellRmRfRule

DEFAULT_RULE_CLASSES: list[type[BaseRule]] = [
    # 高危 DENY 类(永远拒)
    ShellRmRfRule,
    ShellCurlPipeShRule,
    ShellDdBlockDeviceRule,
    ShellFormatDiskRule,
    DbDropTableRule,
    FileWriteOutsideCwdRule,
    # 中危 REQUIRE_APPROVAL 类(配额内可放过)
    ShellChmodUnsafeRule,
    ShellGitPushForceRule,
    DbTruncateTableRule,
    FileWriteSecretsRule,
    HttpPostUnsafeRule,
    NetworkExfilRule,
    # self-mod 特殊:默认 DENY,enable_self_mod=True 时 wave 3 升级为 REQUIRE_APPROVAL
    SelfModifyChariotRule,
]


def default_rules() -> list[BaseRule]:
    """工厂方法:每次构造一组新实例,避免共享状态污染。"""
    return [cls() for cls in DEFAULT_RULE_CLASSES]


__all__ = [
    "DEFAULT_RULE_CLASSES",
    "DbDropTableRule",
    "DbTruncateTableRule",
    "FileWriteOutsideCwdRule",
    "FileWriteSecretsRule",
    "HttpPostUnsafeRule",
    "NetworkExfilRule",
    "SelfModifyChariotRule",
    "ShellChmodUnsafeRule",
    "ShellCurlPipeShRule",
    "ShellDdBlockDeviceRule",
    "ShellFormatDiskRule",
    "ShellGitPushForceRule",
    "ShellRmRfRule",
    "default_rules",
]
