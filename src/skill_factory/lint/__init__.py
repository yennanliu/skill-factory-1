"""`sf lint`: deterministic checks distilled from good skills (design doc §6)."""

from skill_factory.lint.engine import LintReport, lint_skill, lint_skills
from skill_factory.lint.rules import RULES, Finding, Rule

__all__ = ["RULES", "Finding", "LintReport", "Rule", "lint_skill", "lint_skills"]
