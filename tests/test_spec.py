from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from skill_factory.models.spec import Spec, bump, plugin_for
from skill_factory.spec import SkillError, discover, load_skill, parse_frontmatter

BASE = {
    "name": "x",
    "version": "1.0.0",
    "tier": "core",
    "owner": "o",
    "goal": "g",
    "triggers": ["t"],
}


def test_minimal_spec_gets_defaults() -> None:
    s = Spec.model_validate(BASE)
    assert s.budget.max_body_lines == 500
    assert s.evals.min_pass_rate == 0.7
    assert s.side_effects == "none"
    assert s.plugin_name == "sf-core"


@pytest.mark.parametrize(
    ("patch", "msg"),
    [
        ({"name": "Bad_Name"}, "pattern"),
        ({"version": "1.0"}, "pattern"),
        ({"tier": "domain"}, "requires a 'domain'"),
        ({"domain": "x"}, "only allowed with tier 'domain'"),
        ({"triggers": []}, "at least 1"),
        ({"unknown": 1}, "Extra inputs"),
        ({"evals": {"min_pass_rate": 0.9, "max_pass_rate": 0.5}}, "min_pass_rate"),
    ],
)
def test_invalid_specs(patch: dict[str, object], msg: str) -> None:
    with pytest.raises(ValidationError, match=msg):
        Spec.model_validate(BASE | patch)


def test_plugin_mapping() -> None:
    assert plugin_for("always-on", None) == "sf-platform"
    assert plugin_for("core", None) == "sf-core"
    assert plugin_for("experimental", None) == "sf-experimental"
    assert plugin_for("domain", "leetcode") == "sf-domain-leetcode"


@pytest.mark.parametrize(
    ("part", "expected"), [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]
)
def test_bump(part: str, expected: str) -> None:
    assert bump("1.2.3", part) == expected  # type: ignore[arg-type]


def test_parse_frontmatter() -> None:
    fm, body = parse_frontmatter("---\nname: a\ndescription: b\n---\n\n# Title\n")
    assert fm == {"name": "a", "description": "b"}
    assert body == "# Title\n"
    assert parse_frontmatter("# no fm\n") == ({}, "# no fm\n")


def test_load_valid_skills(repo: Path) -> None:
    names = [p.name for p in discover(repo)]
    assert names == ["alpha", "beta"]
    beta = load_skill(repo / "skills" / "beta")
    assert beta.spec.plugin_name == "sf-domain-demo"
    assert beta.frontmatter["name"] == "beta"


def test_name_mismatches_are_reported(repo: Path) -> None:
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text().replace("name: alpha", "name: other"))
    with pytest.raises(SkillError, match=re.escape("SKILL.md name 'other' != spec.name 'alpha'")):
        load_skill(repo / "skills" / "alpha")


def test_missing_script_is_reported(repo: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "tools:\n  scripts: [scripts/run.sh]\n")
    with pytest.raises(SkillError, match=re.escape("scripts/run.sh does not exist")):
        load_skill(repo / "skills" / "alpha")


def test_missing_files(repo: Path) -> None:
    (repo / "skills" / "alpha" / "spec.yaml").unlink()
    with pytest.raises(SkillError, match=re.escape("missing spec.yaml")):
        load_skill(repo / "skills" / "alpha")
