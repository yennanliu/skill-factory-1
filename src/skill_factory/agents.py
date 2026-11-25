"""Managed agents: skills run headless on a schedule, each with an outcome metric (design §9.3).

Definitions live in managed/<name>.yaml:

    name: weekly-skill-digest
    description: ...
    plugins: [sf-platform]          # built plugins loaded with --plugin-dir
    prompt: |
      ...
    model: claude-haiku-5-5
    max_turns: 20
    budget_usd: 0.5
    allowed_tools: [Read, Glob, Grep, Skill]
    outcome:
      contains: ["..."]             # every string must appear in the final answer
      contains_skill_names: true    # every repo skill name must appear
      max_cost_usd: 0.25            # the run counts as failed above this

`sf agent run` executes `claude -p` in the repo, saves the answer and a record
(cost, turns, outcome checks) under runs/agents/<name>/, and exits 1 if the outcome failed.
Agents here only read; anything with outside effects needs a human checkpoint.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from skill_factory.spec import discover


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contains: list[str] = []
    contains_skill_names: bool = False
    max_cost_usd: float | None = None


class AgentDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    plugins: list[str] = []
    prompt: str
    model: str = "claude-haiku-5-5"
    max_turns: int = Field(20, gt=0)
    budget_usd: float = Field(0.5, gt=0)
    allowed_tools: list[str] = ["Read", "Glob", "Grep", "Skill"]
    outcome: Outcome = Outcome()


@dataclass
class RunRecord:
    name: str
    started: str
    model: str
    cost_usd: float | None
    turns: int | None
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def load(root: Path) -> dict[str, AgentDef]:
    out: dict[str, AgentDef] = {}
    for f in sorted((root / "managed").glob("*.yaml")):
        a = AgentDef.model_validate(YAML(typ="safe").load(f.read_text()))
        if a.name != f.stem:
            raise ValueError(f"{f.name}: name {a.name!r} must match the file name {f.stem!r}")
        if a.name in out:
            raise ValueError(f"duplicate managed agent name {a.name!r}")
        out[a.name] = a
    return out


def command(a: AgentDef, root: Path) -> list[str]:
    cmd = [
        "claude",
        "-p",
        a.prompt,
        "--output-format",
        "json",
        "--model",
        a.model,
        "--max-turns",
        str(a.max_turns),
        "--max-budget-usd",
        f"{a.budget_usd:g}",
        "--setting-sources",
        "project,local",
        "--no-session-persistence",
    ]
    for p in a.plugins:
        cmd += ["--plugin-dir", str(root / "plugins" / p)]
    if a.allowed_tools:
        cmd += ["--allowedTools", *a.allowed_tools]
    return cmd


def check_outcome(a: AgentDef, answer: str, cost: float | None, root: Path) -> dict[str, bool]:
    checks = {f"contains {s!r}": s in answer for s in a.outcome.contains}
    if a.outcome.contains_skill_names:
        for p in discover(root):
            # whole name only: "lc" must not count as mentioned because "lc-coach" is
            pattern = rf"(?<![\w-]){re.escape(p.name)}(?![\w-])"
            checks[f"mentions {p.name}"] = re.search(pattern, answer) is not None
    if a.outcome.max_cost_usd is not None:
        checks[f"cost <= ${a.outcome.max_cost_usd:g}"] = (
            cost is not None and cost <= a.outcome.max_cost_usd
        )
    return checks


def run(a: AgentDef, root: Path, timeout: int = 1800) -> tuple[RunRecord, Path]:
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = root / "runs" / "agents" / a.name
    out_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("claude") is None:
        rec = RunRecord(a.name, started, a.model, None, None, False, error="claude CLI not found")
    else:
        try:
            proc = subprocess.run(
                command(a, root),
                cwd=root,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
            result = json.loads(proc.stdout)
        except subprocess.TimeoutExpired:
            result = {
                "is_error": True,
                "subtype": "timeout",
                "result": f"timed out after {timeout}s",
            }
        except OSError as e:
            result = {"is_error": True, "subtype": "oserror", "result": str(e)}
        except ValueError:
            result = {
                "is_error": True,
                "subtype": "bad_output",
                "result": proc.stderr.strip()[:500],
            }
        answer = str(result.get("result") or "")
        cost = result.get("total_cost_usd")
        (out_dir / f"{started}.md").write_text(answer + "\n")
        if result.get("is_error"):
            rec = RunRecord(
                a.name,
                started,
                a.model,
                cost,
                result.get("num_turns"),
                False,
                error=f"{result.get('subtype')}: {answer[:300]}",
            )
        else:
            checks = check_outcome(a, answer, cost, root)
            rec = RunRecord(
                a.name,
                started,
                a.model,
                cost,
                result.get("num_turns"),
                all(checks.values()),
                checks,
            )
    path = out_dir / f"{started}.json"
    path.write_text(json.dumps(rec.to_json(), indent=2) + "\n")
    return rec, path
