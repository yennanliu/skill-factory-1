from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skill_factory.lint import RULES, lint_skill
from skill_factory.lint.output import as_github, as_json, as_text
from skill_factory.spec import spec_sha256

GOOD_MD = """\
---
name: alpha
description: Does alpha. Use when asked to do alpha.
---

# alpha

1. Run the build and check the exit code is 0.
   ```bash
   make build
   ```
2. Confirm `out/alpha.txt` exists.

## Recovery

| Symptom | Action |
|---|---|
| build fails | read the log, fix, re-run step 1 |
"""

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def good(repo: Path) -> Path:
    skill = repo / "skills" / "alpha"
    (skill / "SKILL.md").write_text(GOOD_MD)
    return skill


def rules_hit(skill: Path) -> set[str]:
    return {f.rule for f in lint_skill(skill).findings}


def set_md(skill: Path, text: str) -> None:
    (skill / "SKILL.md").write_text(text)


def add_spec(skill: Path, text: str) -> None:
    p = skill / "spec.yaml"
    p.write_text(p.read_text() + text)


def test_good_skill_is_clean(good: Path) -> None:
    assert rules_hit(good) == set()


def test_rule_ids_are_unique_and_sequential() -> None:
    assert list(RULES) == [f"SF{i:03d}" for i in range(1, len(RULES) + 1)]


def test_sf001_description_without_when(good: Path) -> None:
    set_md(good, GOOD_MD.replace(" Use when asked to do alpha.", ""))
    assert rules_hit(good) == {"SF001"}


def test_sf002_long_description(good: Path) -> None:
    set_md(good, GOOD_MD.replace("Does alpha.", "Does alpha. " + "word " * 100))
    assert rules_hit(good) == {"SF002"}


def test_sf003_body_too_long(good: Path) -> None:
    add_spec(good, "budget:\n  max_body_lines: 5\n")
    assert rules_hit(good) == {"SF003"}


def test_sf004_no_numbered_steps(good: Path) -> None:
    set_md(good, GOOD_MD.replace("1. Run", "- Run").replace("2. Confirm", "- Confirm"))
    assert "SF004" in rules_hit(good)


def test_sf005_steps_without_verification(good: Path) -> None:
    md = GOOD_MD.replace(" and check the exit code is 0", "").replace("Confirm", "Write")
    set_md(good, md)
    assert rules_hit(good) == {"SF005"}


def test_sf006_bash_without_command_block(good: Path) -> None:
    add_spec(good, "tools:\n  required: [Bash]\n")
    set_md(good, GOOD_MD.replace("   ```bash\n   make build\n   ```\n", ""))
    assert rules_hit(good) == {"SF006"}


def test_sf006_ok_without_bash(good: Path) -> None:
    set_md(good, GOOD_MD.replace("   ```bash\n   make build\n   ```\n", ""))
    assert rules_hit(good) == set()


def test_sf007_no_recovery_table(good: Path) -> None:
    set_md(good, GOOD_MD.split("## Recovery")[0])
    assert rules_hit(good) == {"SF007"}


def test_sf008_mcp_json(good: Path) -> None:
    (good / ".mcp.json").write_text("{}")
    assert rules_hit(good) == {"SF008"}


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-" + "a" * 40,
        "ghp_" + "A" * 36,
        "AKIA" + "B" * 16,
        'password = "hunter2hunter2"',
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_sf009_secrets(good: Path, secret: str) -> None:
    (good / "references").mkdir()
    (good / "references" / "notes.md").write_text(f"x\n{secret}\n")
    findings = lint_skill(good).findings
    assert [f.rule for f in findings] == ["SF009"]
    assert findings[0].file == "references/notes.md"
    assert findings[0].line == 2


@pytest.mark.parametrize(
    "benign",
    [
        "SECRET_KEY: str",
        "password: str = Field(..., min_length=8)",
        'token = "${GITHUB_TOKEN}"',
        'api_key = "<your-key-here>"',
    ],
)
def test_sf009_ignores_code_examples(good: Path, benign: str) -> None:
    set_md(good, GOOD_MD + f"\n```python\n{benign}\n```\n")
    assert rules_hit(good) == set()


def test_sf010_shared_tmp(good: Path) -> None:
    set_md(good, GOOD_MD.replace("make build", "make build > /tmp/build.log"))
    assert rules_hit(good) == {"SF010"}


def test_sf010_allows_mktemp_and_tmpdir_vars(good: Path) -> None:
    set_md(good, GOOD_MD.replace("make build", 'LOG="$(mktemp)"; make build > "$TMPDIR/x"'))
    assert rules_hit(good) == set()


def test_sf011_missing_and_non_executable_scripts(good: Path) -> None:
    set_md(good, GOOD_MD.replace("make build", "scripts/build.sh"))
    assert rules_hit(good) == {"SF011"}
    (good / "scripts").mkdir()
    script = good / "scripts" / "build.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)
    assert [f.message for f in lint_skill(good).findings] == ["scripts/build.sh is not executable"]
    os.chmod(script, 0o755)
    assert rules_hit(good) == set()


def test_sf012_spec_hash_drift(good: Path) -> None:
    sha = spec_sha256(good / "spec.yaml")
    set_md(good, GOOD_MD + f"\n<!-- sf:spec-sha256={sha} -->\n")
    assert rules_hit(good) == set()
    add_spec(good, "outputs: [x]\n")
    assert rules_hit(good) == {"SF012"}


def test_sf013_external_side_effects_need_mocks(good: Path) -> None:
    add_spec(good, "side_effects: external\n")
    assert rules_hit(good) == {"SF013"}
    (good / "evals").mkdir()
    (good / "evals" / "c.yaml").write_text("id: c\nmocks:\n  scripts/x.sh: {stdout: ok}\n")
    assert rules_hit(good) == set()


def test_waivers_move_findings_aside(good: Path) -> None:
    set_md(good, GOOD_MD.split("## Recovery")[0])
    add_spec(good, "lint_waivers: [SF007]\n")
    report = lint_skill(good)
    assert report.findings == []
    assert [f.rule for f in report.waived] == ["SF007"]


def test_unknown_waiver_is_an_error(good: Path) -> None:
    add_spec(good, "lint_waivers: [SF999]\n")
    assert [f.message for f in lint_skill(good).findings] == ["unknown rule in lint_waivers: SF999"]


def test_invalid_skill_reports_sf000(good: Path) -> None:
    set_md(good, "no frontmatter\n")
    report = lint_skill(good)
    assert {f.rule for f in report.errors} == {"SF000"}


def test_output_formats(good: Path) -> None:
    set_md(good, GOOD_MD.replace("make build", "make > /tmp/x.log"))
    report = lint_skill(good)
    assert "skills/alpha/SKILL.md:10: error SF010" in as_text(report)
    assert json.loads(as_json(report))["findings"][0]["rule"] == "SF010"
    assert as_github(report).startswith(
        "::error file=skills/alpha/SKILL.md,line=10,title=sf lint::"
    )


def test_repo_skills_lint_clean() -> None:
    from skill_factory.lint import lint_skills
    from skill_factory.spec import discover

    report = lint_skills(discover(ROOT))
    assert report.findings == [], [f"{f.skill} {f.rule} {f.message}" for f in report.findings]


def test_sf006_accepts_indented_fences(good: Path) -> None:
    add_spec(good, "tools:\n  required: [Bash]\n")
    md = GOOD_MD.replace("   ```bash\n   make build\n   ```\n", "")
    md = md.replace("2. Confirm", "2. Run it:\n\n   ```bash\n   make check\n   ```\n\n   Confirm")
    set_md(good, md)
    assert rules_hit(good) == set()


def test_prose_mentions_are_a_warning_not_an_error(good: Path) -> None:
    set_md(good, GOOD_MD + "\nFor example `scripts/build.sh` of another skill may fail.\n")
    findings = lint_skill(good).findings
    assert [(f.rule, f.severity) for f in findings] == [("SF014", "warning")]


def test_sf006_accepts_indented_and_tilde_fences(good: Path) -> None:
    add_spec(good, "tools:\n  required: [Bash]\n")
    assert rules_hit(good) == set()  # GOOD_MD's fence is indented under step 1
    set_md(good, GOOD_MD.replace("   ```bash\n   make build\n   ```", "~~~bash\nmake build\n~~~"))
    assert rules_hit(good) == set()


def test_sf005_last_step_does_not_borrow_later_sections(good: Path) -> None:
    md = GOOD_MD.replace(" and check the exit code is 0", "").replace("2. Confirm", "2. Write")
    md = md.replace("| build fails |", "| check the log, confirm, validate |")
    set_md(good, md)
    assert "SF005" in rules_hit(good)


def test_step_n_headings_count_as_steps(good: Path) -> None:
    md = GOOD_MD.replace("1. Run", "## Step 1: Run").replace("2. Confirm", "## Step 2 — Confirm")
    set_md(good, md)
    assert "SF004" not in rules_hit(good)


def test_sf002_counts_cjk_characters(good: Path) -> None:
    set_md(good, GOOD_MD.replace("Does alpha.", "做" * 150 + "。"))
    assert "SF002" in rules_hit(good)


def test_github_annotations_are_escaped(good: Path) -> None:
    from skill_factory.lint.rules import Finding

    report = lint_skill(good)
    report.findings.append(Finding("SF000", "error", "a,b", "line1\nline2 100%", file="x:y"))
    out = as_github(report)
    assert "file=skills/a%2Cb/x%3Ay" in out and "line1%0Aline2 100%25" in out
    assert "\nline2" not in out
