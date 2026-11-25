"""Run `claude plugin eval` for one skill and store the raw + summarised results."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from skill_factory.eval.result import SkillSummary, parse
from skill_factory.eval.stage import case_dirs, stage_skill
from skill_factory.spec import SkillDir

DEFAULT_BUDGET_USD = 2.0
# Tools `claude plugin eval` removes unless granted (read-only tools are always available).
GATED_TOOLS = {"Bash", "Write", "Edit", "WebFetch", "WebSearch", "NotebookEdit"}


def default_grants(skill: SkillDir) -> list[str]:
    """Gated tools the skill says it needs (spec tools.required minus tools.forbidden)."""
    required = [t for t in skill.spec.tools.required if t.split("(", 1)[0] in GATED_TOOLS]
    return [t for t in required if t not in skill.spec.tools.forbidden]


class EvalError(Exception):
    pass


@dataclass(frozen=True)
class EvalOptions:
    runs: int | None = None  # default: spec.evals.runs
    model: str | None = None  # default: spec.targets.models[0]
    budget_usd: float = DEFAULT_BUDGET_USD  # hard ceiling passed to --max-cost-usd
    concurrency: int = 1
    case: str | None = None  # glob
    tags: tuple[str, ...] = ()
    ablation: bool = True  # with/without-plugin arms
    allow_tools: tuple[str, ...] = ()
    timeout_seconds: int | None = None  # default: estimated from the cases (see run_timeout)


def default_model(skill: SkillDir, opts: EvalOptions) -> str | None:
    if opts.model is not None:
        return opts.model
    return skill.spec.targets.models[0] if skill.spec.targets.models else None


def run_timeout(skill: SkillDir, opts: EvalOptions, cases: int, case_timeout: int) -> int:
    """Wall-clock budget for one `claude plugin eval`: every run at its case timeout, in waves."""
    if opts.timeout_seconds is not None:
        return opts.timeout_seconds
    runs = opts.runs if opts.runs is not None else skill.spec.evals.runs
    arms = 2 if opts.ablation else 1
    waves = -(-cases * runs * arms // max(opts.concurrency, 1))  # ceil
    return max(600, waves * case_timeout + 300)


def build_command(stage: Path, json_out: Path, skill: SkillDir, opts: EvalOptions) -> list[str]:
    runs = opts.runs if opts.runs is not None else skill.spec.evals.runs
    model = default_model(skill, opts)
    cmd = [
        "claude",
        "plugin",
        "eval",
        str(stage),
        "--runs",
        str(runs),
        "--json",
        str(json_out),
        "--max-cost-usd",
        f"{opts.budget_usd:g}",
        "--concurrency",
        str(opts.concurrency),
        "--threshold",
        "0",  # sf decides pass/fail from the spec's pass-rate policy
        "--ablation",
        "with-without" if opts.ablation else "none",
        "--no-publish",  # the harness publishes HTML reports to claude.ai by default
        "--trust-plugin",  # we stage our own repo's skill; never point sf eval at untrusted code
        "--scaffold",  # cases (and their scaffold scripts) are authored in this repo
    ]
    if model:
        cmd += ["--model", model]
    if opts.case:
        cmd += ["--case", opts.case]
    for tag in opts.tags:
        cmd += ["--tag", tag]
    grants = [*default_grants(skill), *skill.spec.evals.allow_tools, *opts.allow_tools]
    if grants:
        cmd += ["--allow-tools", *dict.fromkeys(grants)]
    return cmd


def _txt(b: bytes | str | None) -> str:
    return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")


def max_case_timeout(cases: list[Path]) -> int:
    """Largest execution.timeout_seconds among the cases (harness default 300)."""
    from ruamel.yaml import YAML

    worst = 300
    for d in cases:
        f = d / "case.yaml"
        if not f.is_file():
            continue
        try:
            doc = YAML(typ="safe").load(f.read_text()) or {}
            worst = max(worst, int((doc.get("execution") or {}).get("timeout_seconds", 300)))
        except (ValueError, TypeError, AttributeError):
            continue
    return worst


def run_eval(
    skill: SkillDir,
    runs_dir: Path,
    opts: EvalOptions,
    *,
    skill_md: str | None = None,
    label: str | None = None,
) -> tuple[SkillSummary, Path]:
    """Stage, run, parse. Returns the summary and the directory the results were written to.

    `skill_md` replaces the staged SKILL.md (ablation variants); `label` suffixes the results dir.
    """
    if shutil.which("claude") is None:
        raise EvalError("claude CLI not found on PATH")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = runs_dir / skill.name / (f"{stamp}-{label}" if label else stamp)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "claude-eval.json"
    with tempfile.TemporaryDirectory(prefix=f"sf-eval-{skill.name}-") as tmp:
        stage = stage_skill(skill, Path(tmp) / "plugin", skill_md)
        cmd = build_command(stage, raw_path, skill, opts)
        cases = case_dirs(stage / "evals")
        timeout = run_timeout(skill, opts, len(cases), max_case_timeout(cases))
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout
            )
        except subprocess.TimeoutExpired as e:
            (out_dir / "claude-eval.log").write_text(
                f"$ {' '.join(cmd)}\ntimed out after {timeout}s\n--- stdout\n{_txt(e.stdout)}"
                f"\n--- stderr\n{_txt(e.stderr)}"
            )
            raise EvalError(
                f"claude plugin eval timed out after {timeout}s; see {out_dir / 'claude-eval.log'}"
            ) from e
    log = f"$ {' '.join(cmd)}\nexit {proc.returncode}\n"
    log += f"--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"
    (out_dir / "claude-eval.log").write_text(log)
    # exit 0 = done, 2 = cost ceiling hit (partial result still written)
    if proc.returncode not in (0, 2) or not raw_path.is_file():
        raise EvalError(
            f"claude plugin eval failed (exit {proc.returncode}); see {out_dir / 'claude-eval.log'}"
        )
    model = default_model(skill, opts)
    summary = parse(
        json.loads(raw_path.read_text()),
        skill=skill.name,
        version=skill.spec.version,
        model=model,
        policy=skill.spec.evals,
    )
    if not opts.ablation:
        summary.notes.append(
            "single-arm run (--no-baseline): `tool_used: Skill` graders count toward the score, "
            "so pass rates differ from baseline runs"
        )
    (out_dir / "summary.json").write_text(json.dumps(summary.to_json(), indent=2) + "\n")
    return summary, out_dir
