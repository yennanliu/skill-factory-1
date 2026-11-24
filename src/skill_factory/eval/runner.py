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
from skill_factory.eval.stage import stage_skill
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
    timeout_seconds: int = 3600


def build_command(stage: Path, json_out: Path, skill: SkillDir, opts: EvalOptions) -> list[str]:
    runs = opts.runs or skill.spec.evals.runs
    model = opts.model or (skill.spec.targets.models[0] if skill.spec.targets.models else None)
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
    grants = [*default_grants(skill), *opts.allow_tools]
    if grants:
        cmd += ["--allow-tools", *dict.fromkeys(grants)]
    return cmd


def run_eval(skill: SkillDir, runs_dir: Path, opts: EvalOptions) -> tuple[SkillSummary, Path]:
    """Stage, run, parse. Returns the summary and the directory the results were written to."""
    if shutil.which("claude") is None:
        raise EvalError("claude CLI not found on PATH")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = runs_dir / skill.name / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "claude-eval.json"
    with tempfile.TemporaryDirectory(prefix=f"sf-eval-{skill.name}-") as tmp:
        stage = stage_skill(skill, Path(tmp) / "plugin")
        cmd = build_command(stage, raw_path, skill, opts)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=opts.timeout_seconds,
        )
    log = f"$ {' '.join(cmd)}\nexit {proc.returncode}\n"
    log += f"--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"
    (out_dir / "claude-eval.log").write_text(log)
    # exit 0 = done, 2 = cost ceiling hit (partial result still written)
    if proc.returncode not in (0, 2) or not raw_path.is_file():
        raise EvalError(
            f"claude plugin eval failed (exit {proc.returncode}); see {out_dir / 'claude-eval.log'}"
        )
    model = opts.model or (skill.spec.targets.models[0] if skill.spec.targets.models else None)
    summary = parse(
        json.loads(raw_path.read_text()),
        skill=skill.name,
        version=skill.spec.version,
        model=model,
        policy=skill.spec.evals,
    )
    (out_dir / "summary.json").write_text(json.dumps(summary.to_json(), indent=2) + "\n")
    return summary, out_dir
