# CLI · Typer 使用指南

> **⚠️ 示例含 Rosetta / 0.1.0 遗留**:文中用 `chariot upstream add/list` 作 Typer
> 子命令示例,但 chariot 已无 `upstream` 子命令;`--protocol` 例子(下面 §4.3 +
> §7)用的是 0.1.0 的 `chariot.shared.protocols.Protocol` 枚举,0.2.0 起 chariot
> 单协议化(只接 `/v1/messages`)已删除该枚举和 `--protocol` 选项。Typer 用法本身
> 仍然适用,照抄时把子命令名 / 枚举类换成实际的那个即可。
> 当前子命令见 `chariot/cli/__main__.py`:`status / start / stop / chat / logs / stats`。
>
> **文件定位**:`chariot/cli/` 下用的 Typer 库、项目约定的命令写法、加新命令 / 新参数 / 新测试的手顺。
> **面向**:第一次改 `chariot` CLI 子命令、或者想新增一条命令时查的自己。
> **前置**:项目已装 `typer>=0.13`(`pyproject.toml` 里有),会 Python 类型注解。

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

已经在用 pydantic / FastAPI 这类类型驱动栈的项目,Typer 几乎零额外学习成本。

---

## 二、入口:`chariot/cli/__main__.py`

```python
app = typer.Typer(
    name="chariot",
    help="chariot — 本地 LLM API 格式转换中枢(CLI)",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

for mod in (status_mod, start_mod, stop_mod, upstream_mod,
            logs_mod, stats_mod, chat_mod):
    mod.register(app)


def main() -> None:
    app()
```

构造参数:

| 参数 | 作用 |
|---|---|
| `name` | 帮助文本标题用的名字(与 `pyproject.toml` 的 `[project.scripts] chariot = ...` 一致) |
| `help` | 根命令的一句话说明;`chariot --help` 顶部那段 |
| `no_args_is_help=True` | 裸跑 `chariot`(不带子命令)时打印 help,而不是报错退出。比 argparse 默认友好 |
| `pretty_exceptions_show_locals=False` | **关键**:异常栈里不印本地变量值,避免 `api_key` / token 出现在日志和终端录屏 |

---

## 三、两种注册模式

### 3.1 简单子命令(一级)

例:`chariot status`、`chariot chat "hi"`

```python
# chariot/cli/commands/status.py
import typer

def status_cmd() -> None:
    """显示 server 状态。"""
    ...

def register(app: typer.Typer) -> None:
    app.command("status", help="显示 server 状态")(status_cmd)
```

### 3.2 二级子命令(分组)

例:`chariot upstream list` / `chariot upstream add ...`

```python
# chariot/cli/commands/upstream.py
import typer

upstream_app = typer.Typer(help="管理 upstreams")

@upstream_app.command("list")
def list_cmd() -> None: ...

@upstream_app.command("add")
def add_cmd(
    name: Annotated[str, typer.Option("--name", help="upstream 名字")],
    ...
) -> None: ...

def register(app_root: typer.Typer) -> None:
    app_root.add_typer(upstream_app, name="upstream")
```

关键点:
- 二级分组用 **独立的 `typer.Typer` 实例** 挂到根 app
- `app.add_typer(sub_app, name="xxx")` 注册为一级命令,sub_app 内的 `@.command(...)` 就是二级

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
protocol: Annotated[str, typer.Option("--protocol", help="messages | completions | responses")] = "messages",
model: Annotated[str | None, typer.Option("--model", help="模型 id;未传按 protocol 取默认")] = None,
api_key: Annotated[str | None, typer.Option("--api-key", help="...")] = None,
```

- 字符串选项默认 `""` 或 `None`(二选一取决于语义)
- 布尔 flag:`Annotated[bool, typer.Option("--verbose")]`,typer 会自动生成 `--verbose / --no-verbose`

### 4.3 enum 限定值

`--protocol messages|completions|responses` 这类有限枚举,用 Python Enum(`chariot.shared.protocols.Protocol`)或运行时校验:

```python
from chariot.shared.protocols import Protocol

try:
    fmt = Protocol(protocol)
except ValueError:
    die(f"--protocol 必须是 messages/completions/responses,收到 {protocol!r}")
```

也可以用 `Enum` 类型让 typer 自动限制,但项目沿用显式 `die()` 渲染中文错误。

---

## 五、加一条新命令(手顺)

以加 `chariot ping` 为例:

1. 新建 `chariot/cli/commands/ping.py`:
   ```python
   import typer
   from chariot.cli.core.render import Renderer

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

**重点**:项目 CLI 测试只验 **typer 接线**(`--help` 全通 / 参数校验 / 退出码),**不真调 server**。真调 server 的集成测试用 `@pytest.mark.integration` 标记,默认跳过。

---

## 七、常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| 新命令不出现 在 `--help` | 忘了 `register(app)` 或 `__main__.py` 没把模块加到 import list | 检查 `__main__.py` 的 import + for 循环 |
| 参数提示不支持中文 | typer 默认用 Rich,Windows 老终端 GBK 会乱码 | 确保控制台用 UTF-8 或切到 Windows Terminal |
| 运行报错泄 `api_key` 值 | `pretty_exceptions_show_locals=True` 在 locals 印变量 | 保持 `False`(项目默认);真要调试用单独 flag 控 |
| `--protocol bogus` 不报错就继续跑 | 没做显式 enum 校验 | 用 `try: Protocol(protocol)` 早失败 |
| REPL 里 typer 命令对 `/reset` 无效 | REPL 是项目自己的 input 循环,不走 typer | REPL 命令在 `cli/core/repl.py` 单独解析,不通过 `app()` |

---

## 八、参考

- Typer 官方:<https://typer.tiangolo.com/>
- Click(底层)文档:<https://click.palletsprojects.com/>
- 项目入口:`chariot/cli/__main__.py`
- 项目命令实现:`chariot/cli/commands/*.py`
- 项目测试:`tests/cli/test_commands.py`
