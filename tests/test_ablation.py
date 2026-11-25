from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from skill_factory.eval import ablation, bench, judge
from skill_factory.eval.result import parse
from skill_factory.eval.stage import stage_skill
from skill_factory.models.spec import EvalPolicy
from skill_factory.spec import load_skill

MD = """\
---
name: alpha
description: Does alpha. Use when asked.
---

# Alpha

## Steps

1. Do it.

```markdown
## Not a section (inside a fence)
```

## Recovery

| Symptom | Action |
|---|---|
| x | y |

<!-- sf:spec-sha256=%s -->
""" % ("a" * 64)


def test_sections_skip_fenced_headings() -> None:
    secs = ablation.sections(MD)
    assert [s.title for s in secs] == ["Steps", "Recovery"]
    assert secs[0].slug == "steps"


def test_without_section_keeps_the_rest_and_the_marker() -> None:
    steps, recovery = ablation.sections(MD)
    out = ablation.without_section(MD, steps)
    assert "## Steps" not in out and "## Recovery" in out and "## Not a section" not in out
    out = ablation.without_section(MD, recovery)
    assert "## Recovery" not in out and out.rstrip().endswith("a" * 64 + " -->")
    assert "## Steps" in out


@pytest.mark.parametrize(
    ("args", "verdict"),
    [
        ((0.9, 0.5, 0.10, 0.09), "keep"),
        ((0.9, 0.5, 0.20, 0.10), "optimize"),
        ((0.8, 0.75, None, None), "shorten or retire"),
        ((0.4, 0.8, None, None), "retire"),
        ((0.8, None, None, None), "no baseline"),
    ],
)
def test_skill_verdict(args: tuple[Any, ...], verdict: str) -> None:
    assert ablation.skill_verdict(*args) == verdict


@pytest.mark.parametrize(
    ("full", "without", "max_rate", "verdict"),
    [
        (0.8, 0.5, 0.95, "keep section"),
        (0.8, 0.75, 0.95, "delete section"),
        (1.0, 1.0, 0.95, "inconclusive (eval too easy)"),
        (1.0, 0.5, 0.95, "keep section"),
        (None, 0.5, 0.95, "keep section"),
    ],
)
def test_section_verdict(
    full: float | None, without: float | None, max_rate: float, verdict: str
) -> None:
    assert ablation.section_verdict(full, without, max_rate) == verdict


def test_report_document() -> None:
    full = {
        "skill": "a",
        "model": "m",
        "pass_rate": 0.8,
        "baseline_pass_rate": 0.4,
        "mean_cost_usd": 0.1,
        "baseline_mean_cost_usd": 0.1,
        "policy": {"max_pass_rate": 0.95},
    }
    doc = ablation.report(
        full,
        [
            {"section": "Steps", "summary": {"pass_rate": 0.5}, "tokens_saved": 50},
            {"section": "Notes", "summary": {"pass_rate": 0.8}, "tokens_saved": 20},
        ],
    )
    assert doc["verdict"] == "keep" and doc["eval_too_easy"] is False
    assert [(r["section"], r["verdict"]) for r in doc["sections"]] == [
        ("Steps", "keep section"),
        ("Notes", "delete section"),
    ]
    assert doc["sections"][0]["delta"] == pytest.approx(-0.3)


def test_stage_with_override(repo: Path, tmp_path: Path) -> None:
    skill_dir = repo / "skills" / "alpha"
    (skill_dir / "evals" / "c").mkdir(parents=True)
    (skill_dir / "evals" / "c" / "case.yaml").write_text("name: c\n")
    stage = stage_skill(load_skill(skill_dir), tmp_path / "s", skill_md="VARIANT")
    assert (stage / "skills" / "alpha" / "SKILL.md").read_text() == "VARIANT"
    assert "VARIANT" not in (skill_dir / "SKILL.md").read_text()


def summary(rates: list[bool], cost: float, fired: int | None = None) -> Any:
    runs = [
        {
            "passed": p,
            "score": float(p),
            "costUsd": cost,
            "graders": [] if fired is None else [{"name": "f", "passed": i < fired}],
        }
        for i, p in enumerate(rates)
    ]
    graders = (
        [] if fired is None else [{"name": "f", "type": "tool_used", "config": {"tool": "Skill"}}]
    )
    raw = {
        "schemaVersion": 1,
        "cases": [{"name": "c", "graders": graders, "arms": {"with": runs, "without": []}}],
    }
    return parse(raw, skill="x", version="1", model=None, policy=EvalPolicy())


def test_bench_recommends_cheapest_passing_model() -> None:
    sums = {
        "haiku": summary([True] * 9 + [False], 0.01, fired=10),
        "sonnet": summary([True] * 10, 0.05),
        "opus": summary([True] * 10, 0.20),
    }
    table = bench.rows(sums, 0.7)
    assert [r.meets_bar for r in table] == [True, True, True]
    assert table[0].fired == 1.0 and table[1].fired is None
    assert bench.recommend(table) == "haiku"
    sums["haiku"] = summary([False] * 10, 0.01)
    assert bench.recommend(bench.rows(sums, 0.7)) == "sonnet"
    none_pass = {"a": summary([True, False, False], 0.1), "b": summary([True, True, False], 0.2)}
    assert bench.recommend(bench.rows(none_pass, 0.9)) == "b"  # best pass rate as fallback
    assert bench.recommend([]) is None


def test_bench_write_default_model(repo: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "targets:\n  models: [sonnet, opus]  # keep\n")
    assert bench.write_default_model(spec, "haiku") == ["haiku", "sonnet", "opus"]
    assert bench.write_default_model(spec, "opus") == ["opus", "haiku", "sonnet"]
    assert "# the skill version" in spec.read_text()
    load_skill(repo / "skills" / "alpha")  # still valid


CAL = """\
- id: a
  criteria: c
  response: r1
  label: pass
- id: b
  criteria: c
  response: r2
  label: FAIL
"""


class Judge:
    name = "fake"

    def __init__(self, verdicts: dict[str, list[str]]) -> None:
        self.verdicts = verdicts

    def generate(self, system: str, user: str, schema: dict[str, Any], model: str) -> Any:
        from skill_factory.compile.backend import Answer

        key = "r1" if "r1" in user else "r2"
        return Answer({"verdict": self.verdicts[key].pop(0), "reason": "because"}, 0.0)


def test_judge_load_grade_and_kappa(tmp_path: Path) -> None:
    (tmp_path / "_calibration").mkdir()
    (tmp_path / "_calibration" / "seed.yaml").write_text(CAL)
    items = judge.load_items(tmp_path)
    assert [(i.id, i.label) for i in items] == [("a", True), ("b", False)]
    fake = Judge({"r1": ["PASS", "FAIL", "PASS"], "r2": ["PASS", "PASS", "FAIL"]})
    graded = [judge.grade(i, fake, "m", votes=3) for i in items]
    assert [g.judge for g in graded] == [True, True]
    assert [g.agrees for g in graded] == [True, False]
    assert graded[0].votes == [True, False, True]


def test_judge_rejects_bad_labels(tmp_path: Path) -> None:
    (tmp_path / "_calibration").mkdir()
    (tmp_path / "_calibration" / "x.yaml").write_text(
        "- {id: a, criteria: c, response: r, label: maybe}\n"
    )
    with pytest.raises(ValueError, match="label must be pass or fail"):
        judge.load_items(tmp_path)


def test_cohen_kappa() -> None:
    assert judge.cohen_kappa([]) is None
    assert judge.cohen_kappa([(True, True), (True, True)]) is None  # chance agreement is total
    assert judge.cohen_kappa([(True, True), (False, False)]) == pytest.approx(1.0)
    assert judge.cohen_kappa([(True, False), (False, True)]) == pytest.approx(-1.0)
    pairs = [(True, True)] * 3 + [(False, False)] * 3 + [(True, False)]
    assert judge.cohen_kappa(pairs) == pytest.approx(0.72, abs=0.01)


def test_repo_calibration_sets_load() -> None:
    root = Path(__file__).resolve().parents[1]
    items = judge.load_items(root / "skills" / "lc-coach" / "evals")
    assert len(items) >= 6 and {i.label for i in items} == {True, False}
    json.dumps([i.__dict__ for i in items])
