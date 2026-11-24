"""Load `skills/<name>/` directories into validated `SkillDir` objects."""

from __future__ import annotations

import hashlib
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


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, body). Missing frontmatter -> ({}, text)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    data = YAML(typ="safe").load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, body


def spec_sha256(spec_path: Path) -> str:
    """Hash of the spec file bytes; recorded in built SKILL.md to detect drift."""
    return hashlib.sha256(spec_path.read_bytes()).hexdigest()


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
        fm, body = parse_frontmatter(skill_md.read_text())
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
    for script in spec.tools.scripts:
        if not (path / script).is_file():
            problems.append(f"tools.scripts: {script} does not exist")

    if problems:
        raise SkillError(name, problems)
    return SkillDir(path=path, spec=spec, frontmatter=fm, body=body)


def discover(root: Path) -> list[Path]:
    """All skill directories under `<root>/skills/`, sorted by name."""
    base = root / "skills"
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
