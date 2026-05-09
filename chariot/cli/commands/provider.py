"""`chariot provider <subcmd>` —— Provider entry CRUD + 默认切换。

子命令:list / show / use / probe / add / update / delete / copy。

0.6.0 库化版:撤旧 ProxyClient,直接走 ProviderRepo + ProviderProber。
0.6.0 起 `chariot model` rename 成 `chariot provider`(跟 ProviderRegistry /
BaseProvider / `providers` 表对齐);v7 起加默认 provider 机制(`is_default` 列)。

二级子命令组(typer.Typer 嵌套):

只读:
- `chariot provider list`:列出 entries(name / type / default 标记)+ 已注册 type
- `chariot provider show [<name>]`:不带参数 = 当前默认;带参数 = 指定 entry 详情
  (api_key 本体打码,只显示来源标识)
- `chariot provider probe <name>`:发 1 条最小请求验通断(**~1 token 费用**;mock 零费用)

切换:
- `chariot provider use <name>`:把 <name> 设为默认;`chariot chat` 不带 `--provider`
  时走它

CRUD:
- `chariot provider add --name X --type Y -o k=v -o k=v ... [-p k=v]`: ?? entry
  ?: `chariot provider add --name ollama-qwen --type anthropic -o model=qwen2.5:1.5b -o base_url=http://127.0.0.1:52806 -o api_key=EMPTY`
- `chariot provider update <name> [--type T] [-o k=v] [-p k=v]`: ?? entry
- `chariot provider delete <name>`: ?? entry
- `chariot provider rm <name>`: ?? entry ??(??)
- `chariot provider copy <name> [--as new-name]`: ??(???? _copy_N)

`-o key=value` / `-p key=value` ????;value ?????????
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import typer

from chariot.agent.exceptions import ConfigError, DuplicateProviderName, ProviderNotFound
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.providers.prober import ProviderProber
from chariot.providers.registry import ProviderRegistry
from chariot.repos.provider_repo import ProviderRepo

provider_app = typer.Typer(
    name="provider",
    help="管理 provider entries(0.3.1 起按 entry name 路由)",
    no_args_is_help=True,
)


# ---------- list ----------


@provider_app.command("list", help="列出 entries(name / type / default 标记)")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = ProviderRepo(session)
        entries = await repo.list_entries()
        default = await repo.get_default()
    default_name = default.name if default is not None else None

    if not entries:
        Renderer.out("(DB 里没有 entry — `chariot provider add` 加一条)")
    else:
        rows = [(e.name, e.type, "*" if e.name == default_name else "") for e in entries]
        Renderer.table(["name", "type", "default"], rows, title="entries")

    types = sorted(ProviderRegistry.known_types())
    Renderer.out(f"已注册 type:{', '.join(types)}")
    if default_name is None and entries:
        Renderer.out(
            "(没有默认 provider — `chariot provider use <name>` 设一个)",
        )


# ---------- show ----------


@provider_app.command("show", help="展示 entry 详情(不带参数 = 当前默认)")
def show_cmd(
    name: Annotated[
        str | None,
        typer.Argument(help="entry 名;省略 = 显示当前默认 provider"),
    ] = None,
) -> None:
    asyncio.run(_show(name))


async def _show(name: str | None) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = ProviderRepo(session)
        if name is None:
            entry = await repo.get_default()
            if entry is None:
                Renderer.die(
                    "没有默认 provider — `chariot provider use <name>` 设一个,"
                    "或 `chariot provider show <name>` 看具体 entry",
                )
                return
            is_default = True
        else:
            entry = await repo.get_entry(name)
            if entry is None:
                Renderer.die(f"未知 entry: {name!r}(`chariot provider list` 看现有 id)")
                return
            default = await repo.get_default()
            is_default = default is not None and default.name == entry.name

    rows: list[tuple[str, str]] = [
        ("name", entry.name),
        ("type", entry.type),
        ("default", "yes" if is_default else "no"),
    ]
    # options:api_key 单独打码,其余字段原样展示
    for k, v in entry.options.items():
        rows.append((f"options.{k}", _redact(k, v)))
    for k, v in entry.params.items():
        rows.append((f"params.{k}", str(v)))
    Renderer.table(["field", "value"], rows, title=f"provider {entry.name}")


def _redact(key: str, value: object) -> str:
    """api_key 字段打码;其它字段原样 str。

    inline `api_key` → `(set, len=N)`(透露长度,便于和 env 区分但不泄露内容);
    `api_key_env` → 原值(env 变量名本身不敏感,知道它便于排查"读哪个 env")。
    """
    if key == "api_key":
        if not isinstance(value, str) or not value:
            return "(empty)"
        return f"(set, len={len(value)})"
    return str(value)


# ---------- use ----------


@provider_app.command(
    "use",
    help="把 <name> 设为默认 provider(`chariot chat` 不带 --provider 时用)",
)
def use_cmd(
    name: Annotated[str, typer.Argument(help="要设为默认的 entry 名")],
) -> None:
    asyncio.run(_use(name))


async def _use(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            await ProviderRepo(session).set_default(name)
        except ProviderNotFound as e:
            Renderer.die(f"切换失败: {e}")
            return
    Renderer.out(f"default → {name}")


# ---------- probe ----------


@provider_app.command("probe", help="探一下指定 entry 通不通(消耗 ~1 token 费用)")
def probe_cmd(
    name: Annotated[str, typer.Argument(help="entry name")],
    model: Annotated[
        str | None,
        typer.Option("--model", help="本次覆盖 entry.options.model(LLM 真实 id)"),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="本次覆盖 entry.options.base_url"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="本次覆盖 entry.options.api_key"),
    ] = None,
) -> None:
    asyncio.run(_probe(name, model=model, base_url=base_url, api_key=api_key))


async def _probe(
    name: str,
    *,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> None:
    import dataclasses

    overrides: dict[str, str] = {}
    if model is not None:
        overrides["model"] = model
    if base_url is not None:
        overrides["base_url"] = base_url
    if api_key is not None:
        overrides["api_key"] = api_key

    async with installed_runtime() as agent:
        async with agent.session_maker() as session:
            entry = await ProviderRepo(session).get_entry(name)
        if entry is None:
            Renderer.die(f"未知 entry: {name!r}(`chariot provider list` 看现有 id)")
            return
        if overrides:
            entry = dataclasses.replace(entry, options={**entry.options, **overrides})
        result = await ProviderProber.probe(entry)

    if result.ok:
        Renderer.out(f"✓ {name} OK ({result.latency_ms} ms)")
    else:
        err = result.error
        code = err.code if err else "unknown"
        message = err.message if err else "(no detail)"
        Renderer.die(f"✗ {name} FAIL ({result.latency_ms} ms) [{code}] {message}")


def _parse_kv(items: list[str], *, label: str) -> dict[str, Any]:
    """`-x key=value` ?? ? dict;value ???? `=` ???(???? `=`)?"""
    result: dict[str, Any] = {}
    for raw in items:
        raw_text = raw.strip()
        if _looks_like_json(raw_text):
            Renderer.die(_kv_error_message(label, raw, json_like=True))
            return {}

        parts = [raw]
        if "=" not in raw and "," in raw:
            parts = [part.strip() for part in raw.split(",") if part.strip()]

        for part in parts:
            if "=" in part:
                k, _, v = part.partition("=")
            elif ":" in part:
                k, _, v = part.partition(":")
            else:
                Renderer.die(_kv_error_message(label, raw))
                return {}
            k = k.strip()
            if not k or not _is_valid_kv_key(k):
                Renderer.die(_kv_error_message(label, raw))
                return {}
            result[k] = v.strip()
    return result


def _looks_like_json(raw: str) -> bool:
    return (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))


def _is_valid_kv_key(key: str) -> bool:
    return all(ch.isalnum() or ch in {"_", ".", "-"} for ch in key)


def _kv_error_message(label: str, raw: str, *, json_like: bool = False) -> str:
    base = (
        f"{label} must be key=value, or comma-separated key:value pairs: {raw!r}\n"
        f"template: chariot provider add --name NAME --type TYPE "
        f"{label} key=value {label} key=value ...\n"
        f"compat: {label} key:value,key:value,..."
    )
    if json_like:
        return base + "\nnote: do not pass a whole JSON blob to provider add"
    return base


# ---------- add ----------


@provider_app.command("add", help="新建 provider entry(写入 DB)")
def add_cmd(
    name: Annotated[str, typer.Option("--name", help="entry 名(用户面 ID,需唯一)")],
    type: Annotated[str, typer.Option("--type", help="provider type(mock / anthropic / ...)")],
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-o",
            "--option",
            help="options key=value;可重复(兼容 key:value,key:value 逗号形式)",
        ),
    ] = None,
    params: Annotated[
        list[str] | None,
        typer.Option(
            "-p", "--param", help="params key=value;可重复(runtime 默认值,如 temperature)"
        ),
    ] = None,
) -> None:
    asyncio.run(_add(name, type, options or [], params or []))


async def _add(name: str, type_: str, options: list[str], params: list[str]) -> None:
    opts = _parse_kv(options, label="-o")
    prms = _parse_kv(params, label="-p")
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await ProviderRepo(session).create(
                    name=name,
                    type=type_,
                    options=opts,
                    params=prms,
                )
        except DuplicateProviderName as e:
            Renderer.die(f"添加失败: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"添加失败: {e}")
            return
    Renderer.out(f"+ {entry.name} (type={entry.type})")


# ---------- update ----------


@provider_app.command("update", help="更新现有 entry(改 type / options / params)")
def update_cmd(
    name: Annotated[str, typer.Argument(help="要改的 entry 名")],
    type: Annotated[
        str | None,
        typer.Option("--type", help="新 type(可选)"),
    ] = None,
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-o",
            "--option",
            help="options key=value;???(?: -o model=qwen2.5:1.5b -o base_url=http://127.0.0.1:52806 -o api_key=EMPTY)",
        ),
    ] = None,
    params: Annotated[
        list[str] | None,
        typer.Option(
            "-p",
            "--param",
            help="params key=value;可重复(整体替换 params,非 merge)",
        ),
    ] = None,
) -> None:
    asyncio.run(_update(name, type, options, params))


async def _update(
    name: str,
    type_: str | None,
    options: list[str] | None,
    params: list[str] | None,
) -> None:
    if type_ is None and options is None and params is None:
        Renderer.die("至少给一个 --type / -o / -p 选项,否则无事可做")
        return
    opts = _parse_kv(options, label="-o") if options is not None else None
    prms = _parse_kv(params, label="-p") if params is not None else None
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await ProviderRepo(session).update(
                    name,
                    type=type_,
                    options=opts,
                    params=prms,
                )
        except ProviderNotFound as e:
            Renderer.die(f"编辑失败: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"编辑失败: {e}")
            return
    Renderer.out(f"~ {entry.name} (type={entry.type})")


# ---------- delete / rm ----------


@provider_app.command("delete", help="删除 entry")
@provider_app.command("rm", help="删除 entry; `delete` 的兼容别名")
def rm_cmd(
    name: Annotated[str, typer.Argument(help="要删的 entry 名")],
) -> None:
    asyncio.run(_rm(name))


async def _rm(name: str) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                await ProviderRepo(session).delete(name)
        except ProviderNotFound as e:
            Renderer.die(f"删除失败: {e}")
            return
    Renderer.out(f"- {name}")


# ---------- copy ----------


@provider_app.command("copy", help="复制 entry(--as 缺省自动 _copy / _copy_N)")
def copy_cmd(
    name: Annotated[str, typer.Argument(help="要复制的源 entry 名")],
    as_name: Annotated[
        str | None,
        typer.Option("--as", help="新 entry 名;缺省 <name>_copy(碰撞自动 _copy_2 / _3)"),
    ] = None,
) -> None:
    asyncio.run(_copy(name, as_name))


async def _copy(name: str, as_name: str | None) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await ProviderRepo(session).copy(name, as_name=as_name)
        except ProviderNotFound as e:
            Renderer.die(f"复制失败: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"复制失败: {e}")
            return
    Renderer.out(f"+ {entry.name} (type={entry.type}, copied from {name})")


def register(app: typer.Typer) -> None:
    app.add_typer(provider_app)
