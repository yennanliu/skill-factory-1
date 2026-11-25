"""`sf` command-line entry point."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from skill_factory import __version__
from skill_factory.gitutil import changed_skills
from skill_factory.lint import RULES, lint_skills
from skill_factory.lint.output import as_github, as_json, as_text
from skill_factory.market import BuildError, build, check, release
from skill_factory.paths import claude_version_pin, repo_root, sf_home
from skill_factory.spec import SkillError, discover, load_skill

app = typer.Typer(
    name="sf",
    help="Skill factory: author, lint, evaluate, measure and improve agent skills.",
    no_args_is_help=True,
    add_completion=False,
)
spec_app = typer.Typer(help="Work with skills/<name>/spec.yaml.", no_args_is_help=True)
app.add_typer(spec_app, name="spec")
console = Console()
err = Console(stderr=True)


def _root() -> Path:
    try:
        return repo_root()
    except FileNotFoundError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e


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


@spec_app.command("validate")
def spec_validate(
    skill: Annotated[str | None, typer.Argument(help="Skill name; omit to validate all.")] = None,
) -> None:
    """Validate spec.yaml + SKILL.md frontmatter for one or all skills."""
    root = _root()
    paths = [root / "skills" / skill] if skill else discover(root)
    table = Table("skill", "version", "tier", "plugin", "status")
    failed = 0
    for path in paths:
        if not path.is_dir():
            err.print(f"[red]error:[/] no such skill: {path.name}")
            raise typer.Exit(2)
        try:
            s = load_skill(path)
        except SkillError as e:
            failed += 1
            table.add_row(path.name, "-", "-", "-", "[red]" + "\n".join(e.problems) + "[/]")
            continue
        table.add_row(s.name, s.spec.version, s.spec.tier, s.spec.plugin_name, "[green]ok[/]")
    console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command("build")
def build_cmd(
    check_only: Annotated[
        bool, typer.Option("--check", help="Fail if generated files are out of date.")
    ] = False,
) -> None:
    """Generate plugins/ and .claude-plugin/marketplace.json from skills/ and market.yaml."""
    root = _root()
    try:
        if check_only:
            problems = check(root)
            if problems:
                for p in problems:
                    err.print(f"[red]stale:[/] {p}")
                err.print("run `uv run sf build` and commit the result")
                raise typer.Exit(1)
            console.print("[green]generated files are up to date[/]")
            return
        result = build(root)
    except BuildError as e:
        for p in e.problems:
            err.print(f"[red]error:[/] {p}")
        raise typer.Exit(1) from e
    for w in result.warnings:
        err.print(f"[yellow]warning:[/] {w}")
    for plugin, skills in result.plugins.items():
        console.print(f"[bold]{plugin}[/]: {', '.join(skills)}")


@app.command("release")
def release_cmd(
    skill: Annotated[str, typer.Argument(help="Skill to release.")],
    bump: Annotated[
        Literal["major", "minor", "patch"], typer.Option(help="Which semver part to bump.")
    ] = "patch",
    no_build: Annotated[bool, typer.Option("--no-build", help="Skip `sf build`.")] = False,
) -> None:
    """Bump a skill's version and its plugin's version, then rebuild."""
    root = _root()
    try:
        r = release(root, skill, bump)
    except SkillError as e:
        for p in e.problems:
            err.print(f"[red]error:[/] {e.skill}: {p}")
        raise typer.Exit(1) from e
    console.print(f"{r.skill}: {r.skill_from} -> {r.skill_to}")
    console.print(f"{r.plugin}: {r.plugin_from} -> {r.plugin_to}")
    if not no_build:
        try:
            build(root)
        except BuildError as e:
            err.print("[yellow]versions were bumped, but the rebuild failed:[/]")
            for p in e.problems:
                err.print(f"[red]error:[/] {p}")
            raise typer.Exit(1) from e
        console.print("rebuilt plugins/")


@app.command("lint")
def lint_cmd(
    skills: Annotated[
        list[str] | None, typer.Argument(help="Skill names; omit to lint all skills.")
    ] = None,
    changed: Annotated[
        bool, typer.Option("--changed", help="Only skills with changed files (git).")
    ] = False,
    base: Annotated[
        str | None,
        typer.Option(help="With --changed: compare against this ref (e.g. origin/main)."),
    ] = None,
    fmt: Annotated[
        Literal["text", "json", "github"], typer.Option("--format", help="Output format.")
    ] = "text",
    strict: Annotated[bool, typer.Option(help="Fail on warnings too.")] = False,
    list_rules: Annotated[bool, typer.Option("--list-rules", help="List rules and exit.")] = False,
) -> None:
    """Lint skills against the house rules (design doc §6)."""
    if list_rules:
        table = Table("id", "name", "severity", "checks")
        for r in RULES.values():
            table.add_row(r.id, r.name, r.severity, r.summary)
        console.print(table)
        return
    root = _root()
    if changed:
        names = changed_skills(root, base)
        if not names:
            console.print("no changed skills")
            return
    else:
        names = skills or [p.name for p in discover(root)]
    paths = []
    for n in names:
        if not (root / "skills" / n).is_dir():
            err.print(f"[red]error:[/] no such skill: {n}")
            raise typer.Exit(2)
        paths.append(root / "skills" / n)
    report = lint_skills(paths)
    render = {"text": as_text, "json": as_json, "github": as_github}[fmt]
    out = render(report)
    if out:
        print(out)
    if report.errors or (strict and report.warnings):
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    """Skill factory CLI."""


if __name__ == "__main__":  # pragma: no cover
    app()
