from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

MARKET = """\
# marketplace for tests
name: test-market
owner: tester
repository: tester/test-market
description: test
plugins:
  sf-core:
    version: 0.1.0  # keep me
    description: core
  sf-domain-demo:
    version: 1.2.3
    description: demo
"""

SPEC = """\
name: {name}
version: 1.0.0  # the skill version
tier: {tier}
{domain}owner: tester
goal: Do the {name} thing.
triggers:
  - "do {name}"
"""

SKILL_MD = """\
---
name: {name}
description: Does {name}. Use when asked to do {name}.
---

# {name}

1. Step one.
"""


def make_skill(root: Path, name: str, tier: str = "core", domain: str | None = None) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True)
    dom = f"domain: {domain}\n" if domain else ""
    (d / "spec.yaml").write_text(SPEC.format(name=name, tier=tier, domain=dom))
    (d / "SKILL.md").write_text(SKILL_MD.format(name=name))
    return d


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal skill-factory repo with two valid skills."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "market.yaml").write_text(MARKET)
    make_skill(tmp_path, "alpha", "core")
    beta = make_skill(tmp_path, "beta", "domain", "demo")
    (beta / "references").mkdir()
    (beta / "references" / "notes.md").write_text("ref\n")
    (beta / "evals").mkdir()
    (beta / "evals" / "case.yaml").write_text("id: c1\n")
    return tmp_path


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")
