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


def apply(p: Planned, agent: Agent) -> None:
    if p.action not in ("install", "update"):
        return
    if not p.source.is_dir():
        raise FileNotFoundError(f"{p.source} missing; run `sf build` first")
    if p.dest.exists():
        shutil.rmtree(p.dest)
    shutil.copytree(p.source, p.dest)
    md = p.dest / "SKILL.md"
    md.write_text(portable_skill_md(md.read_text()))
    (p.dest / MARKER).write_text(f"installed by `sf install --agent {agent}`\n")
