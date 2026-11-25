"""`sf new`: scaffold a skill directory (spec.yaml template, SKILL.md stub, a starter eval)."""

from __future__ import annotations

import re
from pathlib import Path

from skill_factory import yamlio
from skill_factory.models.spec import KEBAB_RE, plugin_for

SPEC_TEMPLATE = """\
name: {name}
version: 0.1.0
tier: {tier}
{domain_line}owner: {owner}
goal: >-
  {goal}
triggers:  # phrases a user would say; they become the description
  - "TODO: a request that should trigger this skill"
outputs:
  - "TODO: what the skill produces"
tools:
  required: [Read, Glob, Grep]
  forbidden: []
  scripts: []  # deterministic helpers under scripts/ (Code Mode)
targets:
  agents: [claude-code]
  models: [claude-sonnet-5-5]
budget:
  max_body_lines: 500
  target_cost_usd: 0.15
evals:
  min_pass_rate: 0.7
  max_pass_rate: 0.95
  runs: 3
side_effects: none  # none | local | external (external requires mocked evals)
"""

SKILL_STUB = """\
---
name: {name}
description: "TODO - run `sf compile {name}` to generate this from spec.yaml. Use when ..."
---

# {name}

Not compiled yet. Fill in spec.yaml, then run `uv run sf compile {name} --apply`.
"""

CASE_TEMPLATE = """\
schema_version: "1.1"
name: smoke
description: TODO - replace with a real prompt for this skill.
tags: [draft]
execution:
  prompt: "TODO: a realistic request"
  max_turns: 15
  allowed_tools: [Read, Glob, Grep, Skill]
graders:
  - name: skill-fired
    type: tool_used
    tool: Skill
"""


class NewSkillError(Exception):
    pass


def new_skill(
    root: Path, name: str, tier: str, domain: str | None, owner: str | None, goal: str | None
) -> Path:
    if not re.match(KEBAB_RE, name):
        raise NewSkillError(f"name must be kebab-case: {name!r}")
    if tier == "domain" and not domain:
        raise NewSkillError("--tier domain needs --domain")
    if tier != "domain" and domain:
        raise NewSkillError("--domain is only valid with --tier domain")
    if domain and not re.match(KEBAB_RE, domain):
        raise NewSkillError(f"domain must be kebab-case: {domain!r}")
    path = root / "skills" / name
    if path.exists():
        raise NewSkillError(f"{path} already exists")

    market_path = root / "market.yaml"
    market = yamlio.load(market_path)
    plugin = plugin_for(tier, domain)  # type: ignore[arg-type]
    if plugin not in market["plugins"]:
        market["plugins"][plugin] = {"version": "0.1.0", "description": f"{domain} skills."}
        yamlio.dump(market, market_path)

    (path / "evals" / "smoke").mkdir(parents=True)
    (path / "spec.yaml").write_text(
        SPEC_TEMPLATE.format(
            name=name,
            tier=tier,
            owner=owner or market["owner"],
            domain_line=f"domain: {domain}\n" if domain else "",
            goal=goal or "TODO: one sentence on what this skill achieves.",
        )
    )
    (path / "SKILL.md").write_text(SKILL_STUB.format(name=name))
    (path / "evals" / "smoke" / "case.yaml").write_text(CASE_TEMPLATE)
    return path
