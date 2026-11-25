"""`sf` command-line entry point."""

from __future__ import annotations

import shutil
import subprocess

import typer
from rich.console import Console

from skill_factory import __version__
from skill_factory.paths import claude_version_pin, repo_root, sf_home

app = typer.Typer(
    name="sf",
    help="Skill factory: author, lint, evaluate, measure and improve agent skills.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _installed_claude_version() -> str | None:
    exe = shutil.which("claude")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # e.g. "2.1.331 (Claude Code)"
    return out.stdout.strip().split(" ", 1)[0] or None


@app.command()
def version() -> None:
    """Show sf, Claude Code and contract-pin versions."""
    console.print(f"sf {__version__}")
    installed = _installed_claude_version()
    console.print(f"claude (installed): {installed or 'not found'}")
    try:
        pin = claude_version_pin(repo_root())
    except FileNotFoundError:
        pin = None
    console.print(f"claude (pinned in .claude-version): {pin or 'n/a'}")
    if installed and pin and installed != pin:
        console.print(
            "[yellow]warning:[/] installed Claude Code differs from the pinned contract version; "
            "re-run scripts/probe_claude.sh and refresh tests/fixtures/claude/."
        )
    console.print(f"SF_HOME: {sf_home()}")


@app.callback()
def main() -> None:
    """Skill factory CLI."""


if __name__ == "__main__":  # pragma: no cover
    app()
