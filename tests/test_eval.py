from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_factory.eval.result import parse
from skill_factory.eval.runner import EvalOptions, build_command, default_grants
from skill_factory.eval.stage import SHARED_DIR, case_dirs, stage_skill
from skill_factory.eval.stats import wilson
from skill_factory.models.spec import EvalPolicy
from skill_factory.spec import load_skill

ROOT = Path(__file__).resolve().parents[1]
PINNED = (ROOT / ".claude-version").read_text().strip()
REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "claude-eval" / PINNED / "lc-coach.json").read_text()
)

CASE = """\
schema_version: "1.1"
name: {name}
execution:
  prompt: "do alpha"
graders:
  - name: said-alpha
    type: regex
    pattern: alpha
"""


@pytest.fixture
def evaluated(repo: Path) -> Path:
    skill = repo / "skills" / "alpha"
    for name in ("one", "two"):
        (skill / "evals" / name).mkdir(parents=True)
        (skill / "evals" / name / "case.yaml").write_text(CASE.format(name=name))
    (skill / "evals" / SHARED_DIR).mkdir()
    (skill / "evals" / SHARED_DIR / "setup.sh").write_text("#!/bin/sh\n")
    (skill / "evals" / "results").mkdir()  # stale harness output must not be staged
    return skill


def test_wilson() -> None:
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(9, 10)
    assert lo == pytest.approx(0.596, abs=1e-3) and hi == pytest.approx(0.982, abs=1e-3)
    lo, hi = wilson(3, 3)
    assert hi == 1.0 and lo == pytest.approx(0.438, abs=1e-3)


def test_stage_builds_a_one_skill_plugin(evaluated: Path, tmp_path: Path) -> None:
    stage = stage_skill(load_skill(evaluated), tmp_path / "stage")
    manifest = json.loads((stage / ".claude-plugin" / "plugin.json").read_text())
    assert manifest == {"name": "sf-eval-alpha", "version": "1.0.0"}
    assert (stage / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (stage / "skills" / "alpha" / "spec.yaml").exists()
    assert not (stage / "skills" / "alpha" / "evals").exists()
    assert not (stage / "evals" / "results").exists()
    for case in ("one", "two"):
        assert (stage / "evals" / case / SHARED_DIR / "setup.sh").is_file()
    assert [d.name for d in case_dirs(stage / "evals")] == ["one", "two"]


def test_stage_requires_cases(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no eval cases"):
        stage_skill(load_skill(repo / "skills" / "alpha"), tmp_path / "stage")


def test_build_command_defaults(evaluated: Path, tmp_path: Path) -> None:
    spec = evaluated / "spec.yaml"
    spec.write_text(
        spec.read_text()
        + "tools:\n  required: [Read, Bash, Write]\n  forbidden: [Write]\n"
        + "targets:\n  models: [claude-sonnet-5-5]\nevals:\n  runs: 4\n"
    )
    skill = load_skill(evaluated)
    assert default_grants(skill) == ["Bash"]
    cmd = build_command(
        tmp_path / "p", tmp_path / "o.json", skill, EvalOptions(allow_tools=("Edit",))
    )
    assert cmd[:4] == ["claude", "plugin", "eval", str(tmp_path / "p")]
    flags = " ".join(cmd)
    for expected in (
        "--runs 4",
        "--model claude-sonnet-5-5",
        "--no-publish",
        "--scaffold",
        "--threshold 0",
        "--ablation with-without",
        "--allow-tools Bash Edit",
    ):
        assert expected in flags
    cmd = build_command(
        tmp_path / "p",
        tmp_path / "o.json",
        skill,
        EvalOptions(runs=1, model="m", ablation=False, case="c*", tags=("x",)),
    )
    flags = " ".join(cmd)
    for expected in ("--runs 1", "--model m", "--ablation none", "--case c*", "--tag x"):
        assert expected in flags


def test_parse_real_harness_output() -> None:
    s = parse(REAL, skill="lc-coach", version="1.0.0", model="m", policy=EvalPolicy())
    assert s.claude_version == PINNED
    assert {c.name for c in s.cases} == {"mock-starts-without-solution", "review-quadratic-two-sum"}
    raw_runs = sum(len(c["arms"]["with"]) for c in REAL["cases"])
    raw_passes = sum(r["passed"] for c in REAL["cases"] for r in c["arms"]["with"])
    assert (s.runs, s.passes) == (raw_runs, raw_passes)
    for c in s.cases:
        assert c.fired_runs is not None and 0 <= c.fired_runs <= c.runs
        assert c.baseline_runs == c.runs
        assert c.mean_cost_usd is not None and c.mean_cost_usd > 0
    assert s.total_cost_usd == pytest.approx(REAL["costUsd"])
    assert json.loads(json.dumps(s.to_json()))["skill"] == "lc-coach"


def _run(passed: bool) -> dict[str, object]:
    return {"score": 1.0 if passed else 0.0, "passed": passed, "costUsd": 0.01, "graders": []}


@pytest.mark.parametrize(
    ("passes", "status"), [(0, "below-min"), (6, "below-min"), (8, "ok"), (10, "too-easy")]
)
def test_status_against_policy(passes: int, status: str) -> None:
    raw = {
        "schemaVersion": 1,
        "cases": [
            {"name": "c", "arms": {"with": [_run(i < passes) for i in range(10)], "without": []}}
        ],
    }
    s = parse(raw, skill="x", version="1.0.0", model=None, policy=EvalPolicy())
    assert s.status == status
    assert s.baseline_pass_rate is None


def test_no_runs_and_notes() -> None:
    s = parse(
        {"schemaVersion": 2, "partial": True, "cases": []},
        skill="x",
        version="1.0.0",
        model=None,
        policy=EvalPolicy(),
    )
    assert s.status == "no-runs"
    assert any("schemaVersion 2" in n for n in s.notes)
    assert any("partial" in n for n in s.notes)


def test_every_repo_skill_has_a_loadable_eval_suite() -> None:
    """Each case parses as YAML, has a name matching its dir and at least one grader."""
    from ruamel.yaml import YAML

    for skill_dir in sorted((ROOT / "skills").iterdir()):
        evals = skill_dir / "evals"
        assert evals.is_dir(), f"{skill_dir.name} has no evals/"
        dirs = case_dirs(evals)
        assert dirs, f"{skill_dir.name} has no cases"
        for d in dirs:
            case = YAML(typ="safe").load((d / "case.yaml").read_text())
            assert case["schema_version"] == "1.1"
            assert case["name"] == d.name
            assert case["graders"], d
            scaffold = case.get("context", {}).get("scaffold_script")
            if scaffold:
                assert scaffold.startswith(f"{SHARED_DIR}/"), "scaffolds live in evals/_shared/"
                assert (evals / scaffold).is_file(), scaffold
