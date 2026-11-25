from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from skill_factory.market import BuildError, build, check, release
from skill_factory.spec import spec_sha256
from tests.conftest import make_skill

ROOT = Path(__file__).resolve().parents[1]


def test_build_generates_marketplace_and_plugins(repo: Path) -> None:
    result = build(repo)
    assert result.plugins == {"sf-core": ["alpha"], "sf-domain-demo": ["beta"]}

    market = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text())
    assert market["name"] == "test-market"
    assert market["owner"] == {"name": "tester"}
    assert [p["source"] for p in market["plugins"]] == [
        "./plugins/sf-core",
        "./plugins/sf-domain-demo",
    ]

    pj = json.loads(
        (repo / "plugins" / "sf-domain-demo" / ".claude-plugin" / "plugin.json").read_text()
    )
    assert pj["version"] == "1.2.3"
    assert (
        "/plugin install sf-core --marketplace tester/test-market"
        in (repo / "plugins" / "sf-core" / "README.md").read_text()
    )


def test_build_excludes_authoring_files_and_stamps_hash(repo: Path) -> None:
    build(repo)
    beta = repo / "plugins" / "sf-domain-demo" / "skills" / "beta"
    assert (beta / "references" / "notes.md").is_file()
    assert not (beta / "spec.yaml").exists()
    assert not (beta / "evals").exists()
    stamp = (beta / "SKILL.md").read_text().splitlines()[-1]
    sha = spec_sha256(repo / "skills" / "beta" / "spec.yaml")
    assert stamp == f"<!-- sf:spec-sha256={sha} sf:version=1.0.0 -->"


def test_build_is_deterministic_and_check_detects_drift(repo: Path) -> None:
    build(repo)
    assert check(repo) == []
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text() + "2. Step two.\n")
    assert check(repo) == ["plugins/sf-core/skills/alpha/SKILL.md: differs"]
    build(repo)
    assert check(repo) == []


def test_check_reports_missing_build(repo: Path) -> None:
    problems = check(repo)
    assert problems and all("missing in repo" in p for p in problems)


def test_build_removes_skills_that_moved_tier(repo: Path) -> None:
    build(repo)
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text().replace("tier: core", "tier: domain\ndomain: demo"))
    result = build(repo)
    assert result.plugins == {"sf-domain-demo": ["alpha", "beta"]}
    assert not (repo / "plugins" / "sf-core").exists()


def test_undeclared_plugin_fails(repo: Path) -> None:
    make_skill(repo, "gamma", "domain", "nowhere")
    with pytest.raises(BuildError, match="sf-domain-nowhere"):
        build(repo)


def test_invalid_skill_fails_build(repo: Path) -> None:
    (repo / "skills" / "alpha" / "SKILL.md").write_text("no frontmatter\n")
    with pytest.raises(BuildError, match=re.escape("alpha: SKILL.md has no frontmatter")):
        build(repo)


def test_release_bumps_skill_and_plugin_preserving_comments(repo: Path) -> None:
    r = release(repo, "beta", "minor")
    assert (r.skill_from, r.skill_to) == ("1.0.0", "1.1.0")
    assert (r.plugin, r.plugin_from, r.plugin_to) == ("sf-domain-demo", "1.2.3", "1.3.0")
    spec_text = (repo / "skills" / "beta" / "spec.yaml").read_text()
    assert "version: 1.1.0  # the skill version" in spec_text
    market_text = (repo / "market.yaml").read_text()
    assert "# marketplace for tests" in market_text
    assert "version: 0.1.0  # keep me" in market_text  # sf-core untouched


def test_repo_generated_files_are_up_to_date() -> None:
    """The committed plugins/ must match a fresh build of this repo's skills/."""
    assert check(ROOT) == [], "run `uv run sf build` and commit"


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_validates_generated_marketplace(repo: Path) -> None:
    build(repo)
    out = subprocess.run(
        ["claude", "plugin", "validate", str(repo)], capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_plugin_assets_are_copied(repo: Path) -> None:
    (repo / "hooks").mkdir()
    (repo / "hooks" / "hooks.json").write_text('{"hooks": {}}\n')
    market = repo / "market.yaml"
    market.write_text(
        market.read_text() + "  sf-platform:\n    version: 0.1.0\n    assets: [hooks]\n"
    )
    result = build(repo)
    assert result.plugins["sf-platform"] == []
    assert (repo / "plugins" / "sf-platform" / "hooks" / "hooks.json").is_file()
    market.write_text(market.read_text().replace("assets: [hooks]", "assets: [missing]"))
    with pytest.raises(BuildError, match="asset dir 'missing' does not exist"):
        build(repo)


def test_release_checks_the_plugin_before_writing(repo: Path) -> None:
    from skill_factory.spec import SkillError

    market = repo / "market.yaml"
    market.write_text(
        market.read_text().replace(
            "  sf-domain-demo:\n    version: 1.2.3\n    description: demo\n", ""
        )
    )
    spec = repo / "skills" / "beta" / "spec.yaml"
    before = spec.read_text()
    with pytest.raises(SkillError, match=re.escape("not declared in market.yaml")):
        release(repo, "beta", "patch")
    assert spec.read_text() == before  # nothing half-written


def test_build_with_no_plugin_members_keeps_a_valid_tree(repo: Path) -> None:
    build(repo)
    shutil.rmtree(repo / "skills")
    (repo / "skills").mkdir()
    build(repo)  # used to rmtree plugins/ and then crash on the missing staging dir
    assert (repo / "plugins").is_dir() and list((repo / "plugins").iterdir()) == []
    assert check(repo) == []


def test_invalid_market_yaml_is_a_build_error(repo: Path) -> None:
    (repo / "market.yaml").write_text("plugins:\n  - [unclosed\n")
    with pytest.raises(BuildError, match="not valid YAML"):
        build(repo)


@pytest.mark.parametrize(
    ("assets", "msg"),
    [
        ("[../outside]", "outside the repo"),
        ("[a/hooks, b/hooks]", "collides with 'hooks'"),
        ("[skills]", "collides with 'skills'"),
    ],
)
def test_asset_paths_are_validated(repo: Path, assets: str, msg: str) -> None:
    (repo.parent / "outside").mkdir(exist_ok=True)
    for d in ("a/hooks", "b/hooks"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    market = repo / "market.yaml"
    market.write_text(
        market.read_text() + f"  sf-platform:\n    version: 0.1.0\n    assets: {assets}\n"
    )
    with pytest.raises(BuildError, match=re.escape(msg)):
        build(repo)
