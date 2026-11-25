"""`sf install --agent codex|opencode|gemini`: copy built skills into another agent's skills dir.

All three read the shared Agent Skills format (a folder with SKILL.md + name/description
frontmatter). Locations (user scope / project scope), per each agent's docs (2026-11):

  codex     ~/.codex/skills/            .codex/skills/
  opencode  ~/.config/opencode/skills/  .opencode/skills/
  gemini    ~/.gemini/skills/           .gemini/skills/

Claude-only frontmatter (allowed-tools) is dropped. Only skills whose spec lists the agent in
targets.agents are installed unless --all-skills is given. A skill directory is only replaced if
an earlier `sf install` created it (it carries an .sf-installed marker), so hand-written skills
with the same name are never overwritten.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML

from skill_factory.spec import SkillDir, parse_frontmatter

Agent = Literal["codex", "opencode", "gemini"]
TARGET_NAME = {"codex": "codex", "opencode": "opencode", "gemini": "gemini-cli"}
USER_DIRS = {
    "codex": ".codex/skills",
    "opencode": ".config/opencode/skills",
    "gemini": ".gemini/skills",
}
PROJECT_DIRS = {
    "codex": ".codex/skills",
    "opencode": ".opencode/skills",
    "gemini": ".gemini/skills",
}
PORTABLE_KEYS = ("name", "description", "license", "compatibility", "metadata")
MARKER = ".sf-installed"


@dataclass(frozen=True)
class Planned:
    skill: str
    source: Path
    dest: Path
    action: Literal["install", "update", "skip-foreign", "skip-target"]


def target_dir(agent: Agent, scope: Literal["user", "project"], home: Path, cwd: Path) -> Path:
    return (home / USER_DIRS[agent]) if scope == "user" else (cwd / PROJECT_DIRS[agent])


def plan(
    skills: list[SkillDir], built_root: Path, agent: Agent, dest_dir: Path, all_skills: bool = False
) -> list[Planned]:
    out = []
    for s in skills:
        source = built_root / "plugins" / s.spec.plugin_name / "skills" / s.name
        dest = dest_dir / s.name
        if not all_skills and TARGET_NAME[agent] not in s.spec.targets.agents:
            action: Literal["install", "update", "skip-foreign", "skip-target"] = "skip-target"
        elif dest.exists() and not (dest / MARKER).exists():
            action = "skip-foreign"
        else:
            action = "update" if dest.exists() else "install"
        out.append(Planned(s.name, source, dest, action))
    return out


def portable_skill_md(text: str) -> str:
    fm, body = parse_frontmatter(text)
    kept = {k: fm[k] for k in PORTABLE_KEYS if k in fm}
    yaml = YAML()
    yaml.width = 4096
    buf = StringIO()
    yaml.dump(kept, buf)
    return f"---\n{buf.getvalue()}---\n\n{body}"


def check_sources(planned: list[Planned]) -> None:
    missing = [
        p.source for p in planned if p.action in ("install", "update") and not p.source.is_dir()
    ]
    if missing:
        raise FileNotFoundError(f"{', '.join(map(str, missing))} missing; run `sf build` first")


def apply(p: Planned, agent: Agent) -> None:
    """Copy into a temp dir next to the destination, then swap it in: a failure never leaves
    a half-copied or deleted skill behind."""
    if p.action not in ("install", "update"):
        return
    check_sources([p])
    staging = p.dest.with_name(f".{p.dest.name}.sf-new")
    old = p.dest.with_name(f".{p.dest.name}.sf-old")
    for leftover in (staging, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(p.source, staging)
    md = staging / "SKILL.md"
    md.write_text(portable_skill_md(md.read_text()))
    (staging / MARKER).write_text(f"installed by `sf install --agent {agent}`\n")
    if p.dest.exists():
        p.dest.rename(old)
    staging.rename(p.dest)
    if old.exists():
        shutil.rmtree(old)
