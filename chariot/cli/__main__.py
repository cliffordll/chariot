"""`chariot` CLI entrypoint."""

from __future__ import annotations

from typing import Annotated

import typer

from chariot.cli.commands import agent as agent_mod
from chariot.cli.commands import auxiliary as auxiliary_mod
from chariot.cli.commands import chat as chat_mod
from chariot.cli.commands import checkpoint as checkpoint_mod
from chariot.cli.commands import context as context_mod
from chariot.cli.commands import conversation as conversation_mod
from chariot.cli.commands import critic as critic_mod
from chariot.cli.commands import evals as eval_mod
from chariot.cli.commands import guardrail as guardrail_mod
from chariot.cli.commands import job as job_mod
from chariot.cli.commands import logs as logs_mod
from chariot.cli.commands import memory as memory_mod
from chariot.cli.commands import prompt as prompt_mod
from chariot.cli.commands import provider as provider_mod
from chariot.cli.commands import skill as skill_mod
from chariot.cli.commands import stats as stats_mod
from chariot.cli.commands import status as status_mod
from chariot.cli.commands import task as task_mod
from chariot.cli.commands import tool as tool_mod
from chariot.cli.commands import toolset as toolset_mod
from chariot.cli.commands import trace as trace_mod

HELP_CONTEXT: dict[str, list[str]] = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name="chariot",
    help="chariot - local agent CLI",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings=HELP_CONTEXT,
)


@app.callback()
def _root(  # pyright: ignore[reportUnusedFunction]
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="quiet mode: suppress success output"),
    ] = False,
) -> None:
    from chariot.cli.render import Renderer

    Renderer.QUIET = quiet


for mod in (
    status_mod,
    logs_mod,
    stats_mod,
    chat_mod,
    provider_mod,
    tool_mod,
    toolset_mod,
    agent_mod,
    task_mod,
    job_mod,
    conversation_mod,
    memory_mod,
    prompt_mod,
    eval_mod,
    skill_mod,
    checkpoint_mod,
    context_mod,
    trace_mod,
    auxiliary_mod,
    critic_mod,
    guardrail_mod,
):
    mod.register(app)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
