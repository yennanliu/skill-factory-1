"""`sf` command-line entry point."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from skill_factory import __version__
from skill_factory.gitutil import changed_skills
from skill_factory.lint import RULES, lint_skills
from skill_factory.lint.output import as_github, as_json, as_text
from skill_factory.market import BuildError, build, check, release
from skill_factory.paths import claude_version_pin, repo_root, sf_home
from skill_factory.spec import SkillError, discover, load_skill
from skill_factory.telemetry import otel as otel_proc
from skill_factory.telemetry.collector import DEFAULT_PORT

app = typer.Typer(
    name="sf",
    help="Skill factory: author, lint, evaluate, measure and improve agent skills.",
    no_args_is_help=True,
    add_completion=False,
)
spec_app = typer.Typer(help="Work with skills/<name>/spec.yaml.", no_args_is_help=True)
app.add_typer(spec_app, name="spec")
otel_app = typer.Typer(
    help="Local OpenTelemetry collector (http/json, 127.0.0.1).", no_args_is_help=True
)
app.add_typer(otel_app, name="otel")
console = Console()
err = Console(stderr=True)


def _root() -> Path:
    try:
        return repo_root()
    except FileNotFoundError as e:
        err.print(f"[red]error:[/] {escape(str(e))}")
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
            err.print(f"[red]error:[/] no such skill: {escape(path.name)}")
            raise typer.Exit(2)
        try:
            s = load_skill(path)
        except SkillError as e:
            failed += 1
            table.add_row(path.name, "-", "-", "-", "[red]" + escape("\n".join(e.problems)) + "[/]")
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
                    err.print(f"[red]stale:[/] {escape(p)}")
                err.print("run `uv run sf build` and commit the result")
                raise typer.Exit(1)
            console.print("[green]generated files are up to date[/]")
            return
        result = build(root)
    except BuildError as e:
        for p in e.problems:
            err.print(f"[red]error:[/] {escape(p)}")
        raise typer.Exit(1) from e
    for w in result.warnings:
        err.print(f"[yellow]warning:[/] {escape(w)}")
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
            err.print(f"[red]error:[/] {escape(e.skill)}: {escape(p)}")
        raise typer.Exit(1) from e
    console.print(f"{r.skill}: {r.skill_from} -> {r.skill_to}")
    console.print(f"{r.plugin}: {r.plugin_from} -> {r.plugin_to}")
    if not no_build:
        try:
            build(root)
        except BuildError as e:
            err.print("[yellow]versions were bumped, but the rebuild failed:[/]")
            for p in e.problems:
                err.print(f"[red]error:[/] {escape(p)}")
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
        if skills:
            err.print("[red]error:[/] pass skill names or --changed, not both")
            raise typer.Exit(2)
        try:
            names = changed_skills(root, base)
        except subprocess.CalledProcessError as e:
            detail = escape((e.stderr or "").strip() or str(e))  # git output isn't Rich markup
            err.print(f"[red]error:[/] git failed: {detail}")
            raise typer.Exit(2) from e
    else:
        names = skills or [p.name for p in discover(root)]
    paths = []
    for n in names:
        if not (root / "skills" / n).is_dir():
            err.print(f"[red]error:[/] no such skill: {escape(n)}")
            raise typer.Exit(2)
        paths.append(root / "skills" / n)
    report = lint_skills(paths)
    if changed and not names and fmt == "text":
        console.print("no changed skills")
        return
    render = {"text": as_text, "json": as_json, "github": as_github}[fmt]
    out = render(report)
    if out:
        print(out)
    if report.errors or (strict and report.warnings):
        raise typer.Exit(1)


def _otel_dir() -> Path:
    return sf_home() / "otel"


@otel_app.command("up")
def otel_up(port: Annotated[int, typer.Option(help="Port to listen on.")] = DEFAULT_PORT) -> None:
    """Start the collector in the background."""
    try:
        pid = otel_proc.up(_otel_dir(), port)
    except RuntimeError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e
    console.print(f"collector running (pid {pid}) on http://127.0.0.1:{port} -> {_otel_dir()}")
    console.print('next: eval "$(uv run sf otel env)" in the shell you start claude from')


@otel_app.command("down")
def otel_down() -> None:
    """Stop the background collector."""
    console.print("stopped" if otel_proc.down(_otel_dir()) else "not running")


@otel_app.command("status")
def otel_status(
    port: Annotated[int | None, typer.Option(help="Port (default: the running one).")] = None,
) -> None:
    """Show whether the collector is up and how much it has recorded."""
    port = port or otel_proc.stored_port(_otel_dir())
    pid = otel_proc.running_pid(_otel_dir())
    ok = otel_proc.healthy(port)
    console.print(
        f"process: {f'pid {pid}' if pid else 'not running'}; health: {'ok' if ok else 'down'}"
    )
    for name in ("logs", "metrics", "traces"):
        f = _otel_dir() / f"{name}.jsonl"
        if f.is_file():
            console.print(f"  {f}: {f.stat().st_size:,} bytes")
    if not ok:
        raise typer.Exit(1)


@otel_app.command("env")
def otel_env_cmd(
    port: Annotated[int | None, typer.Option(help="Port (default: the running one).")] = None,
) -> None:
    """Print `export` lines that point Claude Code at the collector."""
    for k, v in otel_proc.otel_env(port or otel_proc.stored_port(_otel_dir())).items():
        print(f"export {k}={v}")


@otel_app.command("serve")
def otel_serve(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = DEFAULT_PORT,
) -> None:
    """Run the collector in the foreground (Ctrl-C to stop)."""
    from skill_factory.telemetry.collector import serve

    srv = serve(_otel_dir(), port)
    console.print(f"listening on http://127.0.0.1:{port} -> {_otel_dir()}")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


@app.command("stats")
def stats_cmd(
    since: Annotated[str, typer.Option(help="Window: 30m, 12h, 1d, 2w.")] = "1d",
    events_file: Annotated[
        Path | None, typer.Option(help="Hook events (default $SF_HOME/telemetry/events.jsonl).")
    ] = None,
    otel_logs: Annotated[
        Path | None, typer.Option(help="OTel logs (default $SF_HOME/otel/logs.jsonl).")
    ] = None,
) -> None:
    """Per-skill calls, trigger mix, cost per call and tool errors."""
    from skill_factory.telemetry.ingest import connect
    from skill_factory.telemetry.stats import parse_since, skill_stats

    try:
        start = parse_since(since)
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    ev = events_file or sf_home() / "telemetry" / "events.jsonl"
    ol = otel_logs or _otel_dir() / "logs.jsonl"
    rows = skill_stats(connect(ev, ol), start)
    if not rows:
        console.print(f"no skill activity since {start:%Y-%m-%d %H:%M} UTC")
        console.print(f"(hook events: {ev}; otel logs: {ol})")
        return
    table = Table(
        "skill",
        "calls",
        "model/user",
        "sessions",
        "cost total",
        "cost/call",
        "tool errors",
        "calls from",
        title=f"since {start:%Y-%m-%d %H:%M} UTC",
    )
    for r in rows:
        table.add_row(
            r.skill,
            str(r.calls),
            f"{r.model_calls}/{r.user_calls}",
            str(r.sessions),
            f"${r.cost_usd:.4f}" if r.cost_usd is not None else "-",
            f"${r.cost_per_call:.4f}" if r.cost_per_call is not None else "-",
            str(r.errors),
            r.source,
        )
    console.print(table)


@app.command("eval")
def eval_cmd(
    skills: Annotated[list[str] | None, typer.Argument(help="Skills to evaluate.")] = None,
    all_skills: Annotated[bool, typer.Option("--all", help="Every skill that has evals/.")] = False,
    runs: Annotated[
        int | None, typer.Option(min=1, help="Runs per case (default: spec evals.runs).")
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="Model (default: spec targets.models[0]).")
    ] = None,
    budget: Annotated[float, typer.Option(help="Hard USD ceiling per skill.")] = 2.0,
    jobs: Annotated[
        int, typer.Option("--jobs", "-j", min=1, max=8, help="Concurrent agent runs (1-8).")
    ] = 1,
    case: Annotated[str | None, typer.Option(help="Only cases matching this glob.")] = None,
    tag: Annotated[list[str] | None, typer.Option(help="Only cases with this tag.")] = None,
    no_baseline: Annotated[
        bool, typer.Option("--no-baseline", help="Skip the no-plugin baseline arm.")
    ] = False,
    allow_tools: Annotated[
        list[str] | None, typer.Option("--allow-tools", help="Extra tool grants.")
    ] = None,
) -> None:
    """Run skills' eval suites via `claude plugin eval` and check pass rates against the spec."""
    from skill_factory.eval.runner import EvalError, EvalOptions, run_eval

    root = _root()
    if all_skills:
        names = [p.name for p in discover(root) if (p / "evals").is_dir()]
    elif skills:
        names = skills
    else:
        err.print("[red]error:[/] name skills or pass --all")
        raise typer.Exit(2)
    opts = EvalOptions(
        runs=runs,
        model=model,
        budget_usd=budget,
        concurrency=jobs,
        case=case,
        tags=tuple(tag or ()),
        ablation=not no_baseline,
        allow_tools=tuple(allow_tools or ()),
    )
    table = Table("skill", "status", "pass rate", "95% CI", "baseline", "runs", "cost", "results")
    failed = False
    for name in names:
        try:
            skill = load_skill(root / "skills" / name)
            console.print(f"[bold]{name}[/]: running…")
            summary, out_dir = run_eval(skill, root / "runs", opts)
        except (SkillError, EvalError, FileNotFoundError) as e:
            err.print(f"[red]error:[/] {name}: {e}")
            failed = True
            continue
        lo, hi = summary.interval
        colour = {"ok": "green", "too-easy": "yellow"}.get(summary.status, "red")
        rate = summary.pass_rate
        base = summary.baseline_pass_rate
        table.add_row(
            name,
            f"[{colour}]{summary.status}[/]",
            f"{rate:.0%}" if rate is not None else "-",
            f"{lo:.0%}-{hi:.0%}",
            f"{base:.0%}" if base is not None else "-",
            str(summary.runs),
            f"${summary.total_cost_usd:.2f}",
            str(out_dir.relative_to(root)),
        )
        for c in summary.cases:
            cr = c.pass_rate
            br = c.baseline_pass_rate
            console.print(
                f"  {c.name}: {c.passes}/{c.runs}"
                + (f" ({cr:.0%})" if cr is not None else "")
                + (f", mean score {c.mean_score:.2f}" if c.mean_score is not None else "")
                + (f", skill fired {c.fired_runs}/{c.runs}" if c.fired_runs is not None else "")
                + (f", baseline {br:.0%}" if br is not None else "")
                + (f", {c.errors} errored" if c.errors else "")
            )
        for note in summary.notes:
            err.print(f"  [yellow]note:[/] {note}")
        if summary.status in ("below-min", "no-runs", "partial"):
            failed = True
    console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command("report")
def report_cmd(
    out: Annotated[
        Path | None,
        typer.Option(help="Output HTML file (default <repo>/report/index.html; relative = cwd)."),
    ] = None,
    since: Annotated[str, typer.Option(help="Telemetry window: 12h, 7d, 4w.")] = "30d",
    days: Annotated[int, typer.Option(help="Days in the per-skill sparkline.")] = 14,
    events_file: Annotated[Path | None, typer.Option(help="Hook events file.")] = None,
    otel_logs: Annotated[Path | None, typer.Option(help="OTel logs file.")] = None,
) -> None:
    """Write a static HTML report: usage, cost, errors and latest eval results per skill."""
    from datetime import UTC, datetime

    from skill_factory.report.data import build_rows, usage_by_skill
    from skill_factory.report.render import render
    from skill_factory.telemetry.ingest import connect
    from skill_factory.telemetry.stats import parse_since

    root = _root()
    try:
        start = parse_since(since)
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    now = datetime.now(UTC).replace(tzinfo=None)
    ev = events_file or sf_home() / "telemetry" / "events.jsonl"
    ol = otel_logs or _otel_dir() / "logs.jsonl"
    skills = []
    for path in discover(root):
        try:
            skills.append(load_skill(path))
        except SkillError as e:
            err.print(f"[yellow]skipping {e.skill}:[/] {'; '.join(e.problems)}")
    usage = usage_by_skill(connect(ev, ol), start, now, days)
    rows, others = build_rows(skills, usage, root / "runs")
    html = render(
        rows,
        others,
        since=start,
        generated=now,
        sources={"events": str(ev), "otel": str(ol), "runs": str(root / "runs")},
        days=days,
    )
    if out is None:
        target = root / "report" / "index.html"
    else:
        target = out if out.is_absolute() else Path.cwd() / out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)
    console.print(f"wrote {target} ({len(rows)} skills, {len(others)} other)")


statusline_app = typer.Typer(
    help="Claude Code status line (cost, model, context, skill).", no_args_is_help=True
)
app.add_typer(statusline_app, name="statusline")


@statusline_app.command("install")
def statusline_install(
    settings: Annotated[
        Path | None, typer.Option(help="Settings file (default ~/.claude/settings.json).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Apply without asking.")] = False,
    budget: Annotated[
        float | None, typer.Option(help="Session budget in USD (colours the cost).")
    ] = None,
) -> None:
    """Copy the status-line script to $SF_HOME/bin and set statusLine in settings (shows a diff)."""
    import json

    from skill_factory.statusline import SettingsError, diff, install_script, plan_settings

    root = _root()
    settings = settings or Path.home() / ".claude" / "settings.json"
    script = sf_home() / "bin" / "sf_statusline.py"
    try:
        old, new, _ = plan_settings(settings, script)
    except SettingsError as e:
        err.print(f"[red]error:[/] {escape(str(e))}")
        raise typer.Exit(2) from e
    if old == new:
        console.print("statusLine already set")
    else:
        print(diff(old, new, settings))
        if not yes and not typer.confirm(f"Write {settings}?"):
            raise typer.Exit(1)
    install_script(root, sf_home())
    if old != new:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(new)
    if budget is not None:
        cfg_path = sf_home() / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text()) if cfg_path.is_file() else {}
        except ValueError:
            cfg = None
        if not isinstance(cfg, dict):
            err.print(f"[red]error:[/] {cfg_path} is not a JSON object; fix or remove it")
            raise typer.Exit(2)
        cfg["session_budget_usd"] = budget
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    console.print(f"installed {script}; restart Claude Code to see it")


@app.command("new")
def new_cmd(
    name: Annotated[str, typer.Argument(help="Skill name (kebab-case).")],
    tier: Annotated[
        Literal["always-on", "core", "domain", "experimental"], typer.Option(help="Tier.")
    ] = "experimental",
    domain: Annotated[str | None, typer.Option(help="Domain (required for tier domain).")] = None,
    owner: Annotated[str | None, typer.Option(help="Owner (default: market owner).")] = None,
    goal: Annotated[str | None, typer.Option(help="One-sentence goal.")] = None,
) -> None:
    """Scaffold skills/<name>/ with a spec.yaml template, a SKILL.md stub and a starter eval."""
    from skill_factory.compile.new import NewSkillError, new_skill

    root = _root()
    try:
        path = new_skill(root, name, tier, domain, owner, goal)
    except NewSkillError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    console.print(f"created {path.relative_to(root)}/ (spec.yaml, SKILL.md stub, evals/smoke)")
    console.print(f"next: edit spec.yaml, then `uv run sf compile {name}`")


@app.command("compile")
def compile_cmd(
    skill: Annotated[str | None, typer.Argument(help="Skill to compile.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Overwrite SKILL.md.")] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Fail if any compiled skill's spec changed (CI).")
    ] = False,
    backend: Annotated[
        Literal["claude-cli", "api"], typer.Option(help="claude-cli (default) or api.")
    ] = "claude-cli",
    model: Annotated[str, typer.Option(help="Authoring model.")] = "claude-opus-5-5",
    retries: Annotated[int, typer.Option(min=0, help="Lint-feedback retries.")] = 2,
) -> None:
    """Compile spec.yaml into SKILL.md (+ draft eval cases); writes SKILL.md.new unless --apply."""
    from skill_factory.compile.backend import BackendError, get_backend
    from skill_factory.compile.core import compile_skill, diff, stale_skills, write_outputs

    root = _root()
    if check:
        stale = stale_skills(root)
        for name, reason in stale:
            err.print(f"[red]stale:[/] {name}: {reason}; run `uv run sf compile {name} --apply`")
        if stale:
            raise typer.Exit(1)
        console.print("[green]compiled skills match their specs[/]")
        return
    if not skill:
        err.print("[red]error:[/] name a skill, or pass --check")
        raise typer.Exit(2)
    try:
        s = load_skill(root / "skills" / skill)
    except SkillError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    console.print(f"compiling {skill} with {model} via {backend}…")
    try:
        result = compile_skill(root, s, get_backend(backend), model, retries)
    except BackendError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e
    has_errors = any(f.severity == "error" for f in result.findings)
    if apply and has_errors:
        err.print("[yellow]lint errors remain: writing SKILL.md.new instead of overwriting[/]")
    target, cases = write_outputs(s, result, apply and not has_errors)
    if not apply:
        print(diff(s.skill_md.read_text(), result.skill_md, f"skills/{skill}/SKILL.md"))
    cost = f", ${result.cost_usd:.3f}" if result.cost_usd is not None else ""
    console.print(f"{result.attempts} attempt(s){cost}; wrote {target.relative_to(root)}")
    for c in cases:
        console.print(f"  draft eval: {c.relative_to(root)}")
    for f in result.findings:
        err.print(f"  [yellow]lint {f.severity}:[/] {f.rule} {f.message}")
    if not apply:
        console.print(f"review, then `uv run sf compile {skill} --apply` (or move SKILL.md.new)")
    if has_errors:
        raise typer.Exit(1)


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


@app.command("ablate")
def ablate_cmd(
    skill: Annotated[str, typer.Argument(help="Skill to ablate.")],
    sections_: Annotated[
        list[str] | None, typer.Option("--section", help="Only these section titles (repeatable).")
    ] = None,
    runs: Annotated[int | None, typer.Option(help="Runs per case per arm.")] = None,
    model: Annotated[str | None, typer.Option(help="Model (default: spec).")] = None,
    budget: Annotated[float, typer.Option(help="USD ceiling per arm.")] = 2.0,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="Concurrent runs.")] = 4,
) -> None:
    """Measure what the skill and each `##` section contribute; keep/optimize/shorten/retire."""
    import json as _json
    from datetime import UTC, datetime

    from skill_factory.eval import ablation
    from skill_factory.eval.runner import EvalError, EvalOptions, run_eval
    from skill_factory.report.data import approx_tokens

    root = _root()
    s = load_skill(root / "skills" / skill)
    text = s.skill_md.read_text()
    secs = ablation.sections(text)
    if sections_:
        secs = [x for x in secs if x.title in sections_]
        missing = set(sections_) - {x.title for x in secs}
        if missing:
            err.print(f"[red]error:[/] no such section(s): {', '.join(sorted(missing))}")
            raise typer.Exit(2)
    opts = EvalOptions(runs=runs, model=model, budget_usd=budget, concurrency=jobs)
    try:
        console.print(f"full skill + no-skill baseline ({len(secs)} section variants follow)…")
        full, _ = run_eval(s, root / "runs", opts, label="ablation-full")
    except EvalError as e:  # without the full run there is nothing to compare against
        err.print(f"[red]error:[/] {escape(str(e))}")
        raise typer.Exit(1) from e
    variants = []
    for sec in secs:  # one failed variant must not throw away the others
        console.print(f"  without '{sec.title}'…")
        variant_md = ablation.without_section(text, sec)
        saved = approx_tokens(text) - approx_tokens(variant_md)
        try:
            summary, _ = run_eval(
                s,
                root / "runs",
                EvalOptions(**{**opts.__dict__, "ablation": False}),
                skill_md=variant_md,
                label=f"ablation-minus-{sec.slug}",
            )
        except EvalError as e:
            err.print(f"  [red]error:[/] {escape(str(e))}")
            variants.append({"section": sec.title, "error": str(e), "tokens_saved": saved})
            continue
        variants.append({"section": sec.title, "summary": summary.to_json(), "tokens_saved": saved})
    doc = ablation.report(full.to_json(), variants)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = root / "runs" / skill / f"{stamp}-ablation.json"
    out.write_text(_json.dumps(doc, indent=2) + "\n")
    console.print(
        f"[bold]{skill}[/]: {doc['verdict']} (with {_pct(doc['pass_rate'])}, "
        f"without {_pct(doc['baseline_pass_rate'])}; margin ±{ablation.LIFT_MARGIN:.0%})"
    )
    table = Table("section removed", "pass rate", "Δ", "~tokens saved", "verdict")
    for r in doc["sections"]:
        delta = "-" if r["delta"] is None else f"{r['delta']:+.0%}"
        table.add_row(
            r["section"], _pct(r["pass_rate"]), delta, str(r["tokens_saved"]), r["verdict"]
        )
    console.print(table)
    if doc["eval_too_easy"]:
        console.print(
            "[yellow]note:[/] the full skill is above max_pass_rate, so the suite can't "
            "show what a section contributes; add harder cases first."
        )
    if any(r["verdict"] == "delete section" for r in doc["sections"]):
        console.print(
            "[yellow]note:[/] sections are removed one at a time; delete one, then "
            "re-run the ablation before deleting another."
        )
    console.print(f"wrote {out.relative_to(root)}")


@app.command("bench")
def bench_cmd(
    skill: Annotated[str, typer.Argument(help="Skill to benchmark.")],
    models: Annotated[str, typer.Option(help="Comma-separated model ids.")] = (
        "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"
    ),
    runs: Annotated[int | None, typer.Option(help="Runs per case.")] = None,
    budget: Annotated[float, typer.Option(help="USD ceiling per model.")] = 2.0,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="Concurrent runs.")] = 4,
    write: Annotated[
        bool, typer.Option("--write", help="Make the recommendation the spec's default model.")
    ] = False,
) -> None:
    """Run the suite on several models; recommend the cheapest that meets min_pass_rate."""
    from skill_factory.eval import bench
    from skill_factory.eval.runner import EvalError, EvalOptions, run_eval

    root = _root()
    s = load_skill(root / "skills" / skill)
    summaries = {}
    failed_models = []
    for m in [x.strip() for x in models.split(",") if x.strip()]:
        console.print(f"{m}…")
        opts = EvalOptions(runs=runs, model=m, budget_usd=budget, concurrency=jobs, ablation=False)
        try:
            summaries[m], _ = run_eval(s, root / "runs", opts, label=f"bench-{m}")
        except EvalError as e:
            failed_models.append(m)
            err.print(f"[red]error:[/] {m}: {escape(str(e))}")
    table_rows = bench.rows(summaries, s.spec.evals.min_pass_rate)
    best = bench.recommend(table_rows)
    table = Table(
        "model",
        "pass rate",
        "mean cost/run",
        "skill fired",
        "meets bar",
        title=f"{skill}: min_pass_rate {s.spec.evals.min_pass_rate:.0%}",
    )
    for r in table_rows:
        cost = "-" if r.mean_cost_usd is None else f"${r.mean_cost_usd:.4f}"
        table.add_row(
            ("* " if r.model == best else "") + r.model,
            _pct(r.pass_rate),
            cost,
            _pct(r.fired),
            "yes" if r.meets_bar else "no",
        )
    console.print(table)
    if best is None:
        raise typer.Exit(1)
    best_row = next(r for r in table_rows if r.model == best)
    problems = []
    if failed_models:
        problems.append(f"no result for {', '.join(failed_models)} (the answer may be one of them)")
    if not best_row.meets_bar:
        problems.append(f"no model meets min_pass_rate; {best} is only the best of the rest")
    if any(r.partial for r in table_rows):
        problems.append("some results are partial (cost ceiling hit); raise --budget")
    if problems:
        for p in problems:
            err.print(f"[yellow]not recommending:[/] {p}")
        if write:
            err.print("[yellow]--write skipped[/]")
        raise typer.Exit(1)
    console.print(f"recommended default: {best}")
    if write:
        order = bench.write_default_model(s.spec_path, best)
        console.print(f"spec targets.models -> {order} (rebuild + review the diff)")


judge_app = typer.Typer(help="LLM-judge calibration.", no_args_is_help=True)
app.add_typer(judge_app, name="judge")


@judge_app.command("calibrate")
def judge_calibrate(
    skill: Annotated[str, typer.Argument(help="Skill whose evals/_calibration/ to use.")],
    model: Annotated[str, typer.Option(help="Judge model.")] = "claude-haiku-5-5",
    votes: Annotated[int, typer.Option(min=1, help="Votes per item (odd; majority wins).")] = 3,
    backend: Annotated[
        Literal["claude-cli", "api"], typer.Option(help="claude-cli (default) or api.")
    ] = "claude-cli",
) -> None:
    """Grade author-labelled responses with the judge; report agreement and Cohen's kappa."""
    from skill_factory.compile.backend import BackendError, get_backend
    from skill_factory.eval import judge

    root = _root()
    try:
        items = judge.load_items(root / "skills" / skill / "evals")
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    if not items:
        err.print(f"[red]error:[/] no items in skills/{skill}/evals/_calibration/*.yaml")
        raise typer.Exit(2)
    if votes % 2 == 0:
        err.print("[red]error:[/] --votes must be odd so there is always a majority")
        raise typer.Exit(2)
    be = get_backend(backend)
    graded = []
    table = Table("item", "author", "judge", "votes", "agree")
    try:
        for it in items:
            g = judge.grade(it, be, model, votes)
            graded.append(g)
            table.add_row(
                it.id,
                "pass" if it.label else "fail",
                "pass" if g.judge else "fail",
                "".join("P" if v else "F" for v in g.votes),
                "[green]yes[/]" if g.agrees else "[red]no[/]",
            )
    except BackendError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e
    console.print(table)
    agreement = sum(g.agrees for g in graded) / len(graded)
    kappa = judge.cohen_kappa([(g.item.label, g.judge) for g in graded])
    k = "n/a" if kappa is None else f"{kappa:.2f}"
    console.print(f"agreement {agreement:.0%}, Cohen's kappa {k} ({len(graded)} items, {model})")
    for g in graded:
        if not g.agrees:
            console.print(f"  [yellow]{g.item.id}[/]: {escape(g.majority_reason[:200])}")
    if kappa is None:
        err.print(
            "[yellow]warning:[/] kappa is undefined: every label (and verdict) is the same "
            "class. Add both pass and fail items to the calibration set."
        )
        raise typer.Exit(3)
    if kappa < judge.KAPPA_TRUST:
        err.print(
            f"[yellow]warning:[/] kappa below {judge.KAPPA_TRUST}; don't trust llm graders "
            f"for {skill} until the criteria or labels are fixed"
        )
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    """Skill factory CLI."""


if __name__ == "__main__":  # pragma: no cover
    app()
