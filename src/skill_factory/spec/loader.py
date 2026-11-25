"""Load `skills/<name>/` directories into validated `SkillDir` objects."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from skill_factory import yamlio
from skill_factory.models.spec import Spec


class SkillError(Exception):
    """A skill directory that cannot be loaded or fails validation."""

    def __init__(self, skill: str, problems: list[str]) -> None:
        self.skill = skill
        self.problems = problems
        super().__init__(f"{skill}: " + "; ".join(problems))


@dataclass(frozen=True)
class SkillDir:
    path: Path
    spec: Spec
    frontmatter: dict[str, Any]
    body: str  # SKILL.md without frontmatter

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def skill_md(self) -> Path:
        return self.path / "SKILL.md"

    @property
    def spec_path(self) -> Path:
        return self.path / "spec.yaml"


FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.S | re.M)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, body). Missing frontmatter -> ({}, text).

    Handles CRLF line endings and an empty block (`---\\n---`); the closing `---` must be a
    whole line.
    """
    text = text.replace("\r\n", "\n")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end() :].lstrip("\n")
    data = YAML(typ="safe").load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, body


def spec_sha256(spec_path: Path) -> str:
    """Hash of the spec's *content*, recorded in SKILL.md to detect drift.

    `version` and comments/formatting are excluded, so `sf release` (which only bumps the
    version) doesn't make a compiled SKILL.md look stale.
    """
    try:
        data = YAML(typ="safe").load(spec_path.read_text(encoding="utf-8")) or {}
    except YAMLError:
        return hashlib.sha256(spec_path.read_bytes()).hexdigest()
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "version"}
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tool_names(value: Any) -> set[str]:
    items = value if isinstance(value, list) else str(value or "").split(",")
    return {str(t).strip() for t in items if str(t).strip()}


def tool_problems(spec: Spec, fm: dict[str, Any]) -> list[str]:
    """spec.yaml is the source of truth: SKILL.md `allowed-tools` must agree with tools.*."""
    if "allowed-tools" not in fm:
        return []
    allowed = _tool_names(fm["allowed-tools"])
    required, forbidden = set(spec.tools.required), set(spec.tools.forbidden)
    out = []
    if bad := sorted(allowed & forbidden):
        out.append(f"allowed-tools grants forbidden tools: {', '.join(bad)}")
    if missing := sorted(required - allowed):
        out.append(f"allowed-tools lacks required tools: {', '.join(missing)}")
    if extra := sorted(allowed - required):
        out.append(f"allowed-tools grants tools the spec doesn't list: {', '.join(extra)}")
    return out


def load_skill(path: Path) -> SkillDir:
    name = path.name
    problems: list[str] = []
    spec_file, skill_md = path / "spec.yaml", path / "SKILL.md"
    if not spec_file.is_file():
        raise SkillError(name, ["missing spec.yaml"])
    if not skill_md.is_file():
        raise SkillError(name, ["missing SKILL.md"])

    try:
        spec = Spec.model_validate(yamlio.load(spec_file))
    except YAMLError as e:
        raise SkillError(name, [f"spec.yaml is not valid YAML: {e}"]) from e
    except ValidationError as e:
        msgs = [
            f"spec.{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}" for err in e.errors()
        ]
        raise SkillError(name, msgs) from e

    try:
        fm, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except (YAMLError, ValueError) as e:
        raise SkillError(name, [f"SKILL.md frontmatter: {e}"]) from e

    if spec.name != name:
        problems.append(f"spec.name {spec.name!r} != directory name {name!r}")
    if not fm:
        problems.append("SKILL.md has no frontmatter")
    else:
        if fm.get("name") != spec.name:
            problems.append(f"SKILL.md name {fm.get('name')!r} != spec.name {spec.name!r}")
        if not fm.get("description"):
            problems.append("SKILL.md frontmatter has no description")
    problems += tool_problems(spec, fm)
    for script in spec.tools.scripts:
        if not (path / script).is_file():
            problems.append(f"tools.scripts: {script} does not exist")

    if problems:
        raise SkillError(name, problems)
    return SkillDir(path=path, spec=spec, frontmatter=fm, body=body)


def discover(root: Path) -> list[Path]:
    """All skill directories under `<root>/skills/`, sorted by name ([] if there is none)."""
    base = root / "skills"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
