# CLI · Typer 使用指南

**文件定位**:`chariot/cli/` 下用的 Typer 库、项目约定的命令写法、加新命令 / 新参数 / 新测试的手顺。
**面向**:第一次改 `chariot` CLI 子命令、或者想新增一条命令时查的自己。
**前置**:项目已装 `typer>=0.13`(`pyproject.toml` 里有),会 Python 类型注解。

当前子命令(见 `chariot/cli/__main__.py`):`status / chat / logs / stats / provider / tool / convo`,
其中 `provider` / `tool` / `convo` 是二级子命令组(参见 `chariot/cli/commands/`)。

---

## 一图流

| 问题 | 答案 |
|---|---|
| 为什么用 Typer? | 类型注解即 CLI schema · Rich 渲染自带 · 子命令嵌套直观 |
| 入口在哪? | `chariot/cli/__main__.py` 里的 `app = typer.Typer(...)` |
| 命令怎么注册? | 每个子命令文件 `chariot/cli/commands/<name>.py` 写 `register(app)`,`__main__.py` 遍历调用 |
| 命令参数怎么定义? | 函数签名 + `Annotated[T, typer.Option(...)]` / `typer.Argument(...)` |
| 怎么跑? | `uv run chariot <cmd>` · 打包后 `./dist/chariot.exe <cmd>` |
| 怎么测? | `typer.testing.CliRunner`;见 `tests/cli/test_commands.py` |

---

## 一、为什么选 Typer(vs argparse / click / fire)

| 候选 | 放弃理由 |
|---|---|
| **argparse**(标准库)| 无依赖,但子命令树 / 类型校验 / Rich 渲染全要自己糊;代码量约 3× |
| **click**(Typer 底层)| 装饰器风格要重复写类型(`type=str`),不从 Python 注解推导 |
| **fire** | 反射式,没有统一的 `--help` / option 规约,不适合对外 CLI |
| **Typer** ✅ | 类型注解驱动 + 内置 Rich + 嵌套 Typer = 子命令树天然 + pyright 对参数类型命中 |

已经在用类型驱动栈(pydantic / dataclass)的项目,Typer 几乎零额外学习成本。

---

## 二、入口:`chariot/cli/__main__.py`

```python
HELP_CONTEXT: dict[str, list[str]] = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name="chariot",
    help="chariot — 本地智能体 CLI",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings=HELP_CONTEXT,
)


@app.callback()
def _root(
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="静默模式:抑制成功输出"),
    ] = False,
) -> None:
    """根 callback:处理全局 flag。子命令执行前会先跑这里。"""
    Renderer.QUIET = quiet


for mod in (
    status_mod, logs_mod, stats_mod, chat_mod,
    provider_mod, tool_mod, convo_mod,
):
    mod.register(app)
```

构造参数:

| 参数 | 作用 |
|---|---|
| `name` | 帮助文本标题用的名字(与 `pyproject.toml` 的 `[project.scripts] chariot = ...` 一致) |
| `help` | 根命令的一句话说明;`chariot --help` 顶部那段 |
| `no_args_is_help=True` | 裸跑 `chariot`(不带子命令)时打印 help,而不是报错退出。比 argparse 默认友好 |
| `pretty_exceptions_show_locals=False` | **关键**:异常栈里不印本地变量值,避免 `api_key` / token 出现在日志和终端录屏 |
| `context_settings={"help_option_names": ["-h", "--help"]}` | 让短 flag `-h` 也能触发 help(默认只认 `--help`) |

`@app.callback()` 是根级 hook,典型用法是处理跨命令的全局 flag(如 `--quiet`),每个子命令执行前都会先跑一遍。

---

## 三、两种注册模式

### 3.1 简单子命令(一级)

例:`chariot status` / `chariot chat` / `chariot logs`

```python
# chariot/cli/commands/status.py
import typer

def status_cmd() -> None:
    """显示 chariot 运行状态。"""
    ...

def register(app: typer.Typer) -> None:
    app.command("status", help="显示 chariot 运行状态")(status_cmd)
```

### 3.2 二级子命令(分组)

例:`chariot provider list` / `chariot provider add ...` / `chariot provider probe <name>`
(`chariot/cli/commands/provider.py` 是真实参考)

```python
# chariot/cli/commands/provider.py
import typer

provider_app = typer.Typer(
    name="provider",
    help="管理 provider entries(住 chariot 内置 SQLite)",
    no_args_is_help=True,
)


@provider_app.command("list", help="列出可用 provider + 当前 default")
def list_cmd() -> None: ...


@provider_app.command("add", help="新建 provider entry(写入 DB)")
def add_cmd(
    name: Annotated[str, typer.Option("--name", help="entry 名(用户面 ID,需唯一)")],
    type: Annotated[str, typer.Option("--type", help="provider type(mock / anthropic / ...)")],
    options: Annotated[
        list[str] | None,
        typer.Option("-o", "--option", help="key=value 形式的 options;可重复"),
    ] = None,
) -> None: ...


def register(app: typer.Typer) -> None:
    app.add_typer(provider_app)
```

关键点:
- 二级分组用 **独立的 `typer.Typer` 实例** 挂到根 app
- `app.add_typer(sub_app)` 注册;若 `sub_app` 自带 `name="provider"`,根 app 直接拿;否则 `add_typer(sub_app, name="provider")` 显式指定
- sub_app 内的 `@.command(...)` 就是二级命令
- `no_args_is_help=True` 给 sub_app 加上,让 `chariot provider` 裸跑也打 help

---

## 四、参数定义惯例

项目里统一用 `Annotated[T, typer.Option(...)]` / `typer.Argument(...)`,不用旧式的默认值位参数。

### 4.1 位置参数

```python
def chat_cmd(
    text: Annotated[str | None, typer.Argument(help="要发送的消息;省略进 REPL")] = None,
) -> None: ...
```

- 可选位置参数用 `| None` + 默认 `None`
- 必选位置参数不给默认值

### 4.2 选项参数

```python
@provider_app.command("edit", help="编辑现有 entry(改 type 或 options)")
def edit_cmd(
    name: Annotated[str, typer.Argument(help="要改的 entry 名")],
    type: Annotated[
        str | None,
        typer.Option("--type", help="新 type(可选)"),
    ] = None,
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-o", "--option",
            help="key=value 形式的 options;可重复(整体替换 options,非 merge)",
        ),
    ] = None,
) -> None: ...
```

- 字符串选项默认 `""` 或 `None`(二选一取决于语义;项目里偏 `None` 表示"用户没传")
- 布尔 flag:`Annotated[bool, typer.Option("--verbose")]`,typer 会自动生成 `--verbose / --no-verbose`
- **重复选项**用 `list[str] | None`(`-o k=v -o k2=v2` → `["k=v", "k2=v2"]`),业务侧再 `_parse_kv_options()` 拆成 dict

### 4.3 别名 / 短 flag

`typer.Option("--quiet", "-q", ...)` 注册时把短形式作为额外位置参数传进去;`typer.Option("--as", ...)` 则给 Python 关键字冲突的字段名(`as` 是 Python 关键字)起一个对外名字,Python 端参数仍叫 `as_name`。

### 4.4 受限值集合

如果一个选项只接受有限取值,有两种做法:

**方式 A · 用 Python `Enum`**(typer 自动校验 + 在 `--help` 里列):

```python
from enum import StrEnum

class Format(StrEnum):
    JSON = "json"
    TABLE = "table"

def list_cmd(
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.TABLE,
) -> None: ...
```

调 `--format bogus` typer 会自动以 exit code 2 报错,不需手写校验。

**方式 B · 显式校验 + 中文报错**:

```python
ALLOWED = {"mock", "anthropic"}

def add_cmd(
    type: Annotated[str, typer.Option("--type", help="provider type")] = "mock",
) -> None:
    if type not in ALLOWED:
        Renderer.die(f"--type 必须是 {' / '.join(sorted(ALLOWED))} 之一,收到 {type!r}")
        return
```

项目偏 B —— 错误文案能讲中文 / 加上下文。**但 `chariot provider add --type` 不在客户端校验**,
让 `ProviderRegistry.reserve()` 抛 `ConfigError("unknown_type", ...)`(包含已注册 type 列表)统一管,
避免命令层和 registry 重复维护一份白名单。

---

## 五、加一条新命令(手顺)

以加 `chariot ping` 为例:

1. 新建 `chariot/cli/commands/ping.py`:
   ```python
   import typer
   from chariot.cli.render import Renderer

   def ping_cmd() -> None:
       Renderer.out("pong")

   def register(app: typer.Typer) -> None:
       app.command("ping", help="自检,打印 pong")(ping_cmd)
   ```

2. `chariot/cli/__main__.py` 顶部 import + 注册:
   ```python
   from chariot.cli.commands import ping as ping_mod
   ...
   for mod in (..., ping_mod):
       mod.register(app)
   ```

3. `tests/cli/test_commands.py` 的参数化列表里加 `"ping"`,test_subcommand_help 会自动覆盖。

4. 跑 `uv run chariot ping` 看输出 / `uv run pytest tests/cli/test_commands.py -v` 验结构。

---

## 六、测试:`typer.testing.CliRunner`

参见 `tests/cli/test_commands.py`。核心套路:

```python
from typer.testing import CliRunner
from chariot.cli.__main__ import app

runner = CliRunner()

def test_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "status" in result.output
```

**重点**:项目 CLI 测试只验 **typer 接线**(`--help` 全通 / 参数校验 / 退出码),**不真调上游 LLM**。
真打上游的集成测试用 `@pytest.mark.integration` 标记,默认跳过。

二级命令的 help 也覆盖:

```python
def test_provider_help() -> None:
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    for sub in ("list", "add", "edit", "rm", "probe"):
        assert sub in result.output
```

---

## 七、常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| 新命令不出现 在 `--help` | 忘了 `register(app)` 或 `__main__.py` 没把模块加到 import list | 检查 `__main__.py` 的 import + for 循环 |
| 参数提示不支持中文 | typer 默认用 Rich,Windows 老终端 GBK 会乱码 | 确保控制台用 UTF-8 或切到 Windows Terminal |
| 运行报错泄 `api_key` 值 | `pretty_exceptions_show_locals=True` 在 locals 印变量 | 保持 `False`(项目默认);真要调试用单独 flag 控 |
| `chariot model add -o key` 不报错就继续跑 | `_parse_kv_options` 看到 `=` 缺失会 `die`;但默认 `None` 让 `if "=" not in raw` 走不到 | 给 `-o` 至少传一次合法值,或在 typer 层加 `parser=` 校验 |
| Python 关键字冲突的字段名传不进去 | `as` / `class` 等关键字不能直接当 Python 参数名 | 用 `as_name: ... typer.Option("--as", ...)`,对外 `--as`、对内 `as_name` |
| REPL 里 typer 命令对 `/reset` 无效 | REPL 是项目自己的 input 循环,不走 typer | REPL 命令在 `cli/repl.py` 单独解析,不通过 `app()` |

---

## 八、参考

- Typer 官方:<https://typer.tiangolo.com/>
- Click(底层)文档:<https://click.palletsprojects.com/>
- 项目入口:`chariot/cli/__main__.py`
- 项目命令实现:`chariot/cli/commands/*.py`(`status / chat / logs / stats / provider / tool / convo`)
- 项目测试:`tests/cli/`
