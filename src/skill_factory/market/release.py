"""`sf release`: bump a skill's version and its plugin's version (so installs pick it up)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skill_factory import yamlio
from skill_factory.models.spec import bump
from skill_factory.spec import load_skill

Part = Literal["major", "minor", "patch"]


@dataclass(frozen=True)
class Released:
    skill: str
    skill_from: str
    skill_to: str
    plugin: str
    plugin_from: str
    plugin_to: str


def release(root: Path, skill_name: str, part: Part) -> Released:
    skill = load_skill(root / "skills" / skill_name)
    plugin = skill.spec.plugin_name

    spec_doc = yamlio.load(skill.spec_path)
    old_skill = str(spec_doc["version"])
    spec_doc["version"] = bump(old_skill, part)
    yamlio.dump(spec_doc, skill.spec_path)

    market_path = root / "market.yaml"
    market_doc = yamlio.load(market_path)
    pdef = market_doc["plugins"][plugin]
    old_plugin = str(pdef["version"])
    pdef["version"] = bump(old_plugin, part)
    yamlio.dump(market_doc, market_path)

    return Released(
        skill_name, old_skill, str(spec_doc["version"]), plugin, old_plugin, str(pdef["version"])
    )
