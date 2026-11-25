"""Papercut triage: read the log-papercut inbox, group it, promote papercuts to eval cases."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@dataclass(frozen=True)
class Papercut:
    id: str
    ts: str
    skill: str
    tried: str
    expected: str
    happened: str
    suggested_fix: str
    severity: str
    skill_version: str | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Papercut:
        return cls(
            id=str(d.get("id", "")),
            ts=str(d.get("ts", "")),
            skill=str(d.get("skill", "?")),
            tried=str(d.get("tried", "")),
            expected=str(d.get("expected", "")),
            happened=str(d.get("happened", "")),
            suggested_fix=str(d.get("suggested_fix", "")),
            severity=str(d.get("severity", "low")),
            skill_version=d.get("skill_version"),
        )

    @property
    def short_skill(self) -> str:
        """`sf-core:python-code-reviewer` -> `python-code-reviewer`."""
        return self.skill.rpartition(":")[2]


SEVERITY_ORDER = {"high": 0, "med": 1, "low": 2}


def inbox_path(sf_home: Path) -> Path:
    return sf_home / "papercuts" / "inbox.jsonl"


def state_path(sf_home: Path) -> Path:
    return sf_home / "papercuts" / "state.json"


def load(sf_home: Path) -> list[Papercut]:
    path = inbox_path(sf_home)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(Papercut.from_json(json.loads(line)))
        except (ValueError, AttributeError):
            continue
    return out


def load_state(sf_home: Path) -> dict[str, dict[str, str]]:
    path = state_path(sf_home)
    return dict(json.loads(path.read_text())) if path.is_file() else {}


def mark(sf_home: Path, pid: str, status: str, note: str) -> None:
    state = load_state(sf_home)
    state[pid] = {"status": status, "note": note}
    state_path(sf_home).parent.mkdir(parents=True, exist_ok=True)
    state_path(sf_home).write_text(json.dumps(state, indent=2) + "\n")


def status_of(pid: str, state: dict[str, dict[str, str]]) -> str:
    return state.get(pid, {}).get("status", "open")


def group(cuts: list[Papercut]) -> list[tuple[str, int, Counter[str]]]:
    """(skill, count, severity counts), most reports first."""
    by_skill: dict[str, list[Papercut]] = {}
    for c in cuts:
        by_skill.setdefault(c.short_skill, []).append(c)
    rows = [(s, len(cs), Counter(c.severity for c in cs)) for s, cs in by_skill.items()]
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def find(cuts: list[Papercut], pid: str) -> Papercut | None:
    """Unique match by id prefix. Duplicate lines with the same id (the same report filed twice
    in one second) count as one."""
    matches = [c for c in cuts if c.id.startswith(pid)]
    return matches[0] if matches and len({c.id for c in matches}) == 1 else None


SAFE_RE = re.compile(r"^[\w-]+$")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def promote(root: Path, cut: Papercut) -> Path:
    """Turn a papercut into a draft regression eval case in the skill's evals/."""
    # Ids and skill names come from the inbox: never let them build paths outside skills/.
    if not SAFE_RE.match(cut.short_skill) or not SAFE_RE.match(cut.id):
        raise FileNotFoundError(
            f"refusing unsafe skill/id in papercut: {cut.short_skill!r} {cut.id!r}"
        )
    skill_dir = root / "skills" / cut.short_skill
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"no skill {cut.short_skill!r} in this repo")
    case_dir = skill_dir / "evals" / f"papercut-{cut.id}"
    if case_dir.exists():
        raise FileExistsError(f"{case_dir} already exists")
    case_dir.mkdir(parents=True)
    doc = {
        "schema_version": "1.1",
        "name": f"papercut-{cut.id}",
        "description": f"Regression for papercut {cut.id} ({cut.severity}): {cut.happened}",
        "tags": ["papercut", "draft"],
        "execution": {
            "prompt": f"TODO: a self-contained request reproducing this. Agent tried: {cut.tried}",
            "max_turns": 20,
            "allowed_tools": ["Read", "Glob", "Grep", "Skill"],
        },
        "graders": [
            {
                "name": "expected-behaviour",
                "type": "llm",
                "criteria": f"The agent achieves what was expected: {cut.expected}",
            },
            {"name": "skill-fired", "type": "tool_used", "tool": "Skill"},
        ],
    }
    yaml = YAML()
    yaml.width = 100
    with (case_dir / "case.yaml").open("w") as f:
        # one line each: a newline inside inbox text would otherwise become live YAML
        fix = _one_line(cut.suggested_fix) or "-"
        f.write(f"# From papercut {cut.id} filed {_one_line(cut.ts)}. Suggested fix: {fix}\n")
        f.write(
            "# Make the prompt reproduce the failure, confirm the case FAILS, then fix the skill.\n"
        )
        yaml.dump(doc, f)
    return case_dir / "case.yaml"
