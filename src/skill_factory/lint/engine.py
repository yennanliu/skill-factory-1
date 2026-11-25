"""Run lint rules over skills and collect findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skill_factory.lint.rules import RULES, Finding
from skill_factory.spec import SkillError, load_skill


@dataclass
class LintReport:
    findings: list[Finding] = field(default_factory=list)
    waived: list[Finding] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


def lint_skill(path: Path, report: LintReport | None = None) -> LintReport:
    report = report or LintReport()
    report.skills.append(path.name)
    try:
        skill = load_skill(path)
    except SkillError as e:
        report.findings += [
            Finding("SF000", "error", path.name, p, "fix the spec/frontmatter first", "spec.yaml")
            for p in e.problems
        ]
        return report
    unknown = sorted(set(skill.spec.lint_waivers) - set(RULES))
    for rid in unknown:
        report.findings.append(
            Finding(
                "SF000",
                "error",
                skill.name,
                f"unknown rule in lint_waivers: {rid}",
                file="spec.yaml",
            )
        )
    for rule in RULES.values():
        for f in rule.check(skill):
            (report.waived if rule.id in skill.spec.lint_waivers else report.findings).append(f)
    return report


def lint_skills(paths: list[Path]) -> LintReport:
    report = LintReport()
    for p in paths:
        lint_skill(p, report)
    return report
