"""Parse `claude plugin eval --json` output (schemaVersion 1) and summarise it per skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from skill_factory.eval.stats import wilson
from skill_factory.models.spec import EvalPolicy

SUPPORTED_SCHEMA = 1


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class GraderVerdict(_Lenient):
    name: str
    passed: bool | None = None
    weight: float = 1
    explanation: str = ""
    with_only: bool = Field(False, alias="withOnly")
    scored: bool = True


class Run(_Lenient):
    score: float | None = None
    passed: bool = False
    turns: int | None = None
    cost_usd: float = Field(0.0, alias="costUsd")
    judge_cost_usd: float = Field(0.0, alias="judgeCostUsd")
    duration_seconds: float | None = Field(None, alias="durationSeconds")
    error: Any = None
    graders: list[GraderVerdict] = []


class Arms(_Lenient):
    with_: list[Run] = Field(default_factory=list, alias="with")
    without: list[Run] = []


class GraderDef(_Lenient):
    name: str
    type: str = ""
    config: dict[str, Any] = {}


class Case(_Lenient):
    name: str
    runs_per_case: int | None = Field(None, alias="runsPerCase")
    graders: list[GraderDef] = []
    arms: Arms = Arms()

    def skill_grader_names(self) -> set[str]:
        """Graders that only check whether the Skill tool fired (`tool_used` with `tool: Skill`)."""
        return {
            g.name
            for g in self.graders
            if g.type == "tool_used" and g.config.get("tool") == "Skill"
        }


class EvalOutput(_Lenient):
    schema_version: int = Field(alias="schemaVersion")
    claude_version: str | None = Field(None, alias="claudeVersion")
    cost_usd: float = Field(0.0, alias="costUsd")
    partial: bool = False
    cases: list[Case] = []


Status = Literal["ok", "below-min", "too-easy", "no-runs", "partial"]


@dataclass(frozen=True)
class CaseSummary:
    name: str
    runs: int
    passes: int
    baseline_runs: int
    baseline_passes: int
    errors: int
    fired_runs: int | None  # runs where the Skill tool fired; None if the case doesn't check it
    mean_score: float | None
    mean_cost_usd: float | None
    mean_turns: float | None

    @property
    def pass_rate(self) -> float | None:
        return self.passes / self.runs if self.runs else None

    @property
    def baseline_pass_rate(self) -> float | None:
        return self.baseline_passes / self.baseline_runs if self.baseline_runs else None

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.passes, self.runs)


@dataclass(frozen=True)
class SkillSummary:
    skill: str
    version: str
    model: str | None
    claude_version: str | None
    cases: list[CaseSummary]
    total_cost_usd: float
    partial: bool
    policy: EvalPolicy
    notes: list[str] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return sum(c.runs for c in self.cases)

    @property
    def passes(self) -> int:
        return sum(c.passes for c in self.cases)

    @property
    def pass_rate(self) -> float | None:
        return self.passes / self.runs if self.runs else None

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.passes, self.runs)

    @property
    def baseline_pass_rate(self) -> float | None:
        n = sum(c.baseline_runs for c in self.cases)
        return sum(c.baseline_passes for c in self.cases) / n if n else None

    @property
    def status(self) -> Status:
        if self.partial:
            return "partial"  # a truncated sample isn't representative; never report it as ok
        rate = self.pass_rate
        if rate is None:
            return "no-runs"
        if rate < self.policy.min_pass_rate:
            return "below-min"
        if rate > self.policy.max_pass_rate:
            return "too-easy"
        return "ok"

    def to_json(self) -> dict[str, Any]:
        lo, hi = self.interval
        return {
            "skill": self.skill,
            "version": self.version,
            "model": self.model,
            "claude_version": self.claude_version,
            "status": self.status,
            "pass_rate": self.pass_rate,
            "interval_95": [lo, hi],
            "baseline_pass_rate": self.baseline_pass_rate,
            "runs": self.runs,
            "total_cost_usd": self.total_cost_usd,
            "partial": self.partial,
            "policy": self.policy.model_dump(),
            "cases": [
                {
                    "name": c.name,
                    "runs": c.runs,
                    "passes": c.passes,
                    "pass_rate": c.pass_rate,
                    "interval_95": list(c.interval),
                    "baseline_pass_rate": c.baseline_pass_rate,
                    "errors": c.errors,
                    "mean_score": c.mean_score,
                    "fired_runs": c.fired_runs,
                    "mean_cost_usd": c.mean_cost_usd,
                    "mean_turns": c.mean_turns,
                }
                for c in self.cases
            ],
            "notes": self.notes,
        }


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarise_case(case: Case) -> CaseSummary:
    # Runs that crashed (harness/API errors) say nothing about the skill: count them under
    # `errors`, not as failed attempts.
    errored = [r for r in case.arms.with_ if r.error is not None]
    runs = [r for r in case.arms.with_ if r.error is None]
    base = [r for r in case.arms.without if r.error is None]
    skill_graders = case.skill_grader_names()
    fired = (
        sum(any(g.name in skill_graders and g.passed for g in r.graders) for r in runs)
        if skill_graders
        else None
    )
    return CaseSummary(
        name=case.name,
        runs=len(runs),
        passes=sum(r.passed for r in runs),
        baseline_runs=len(base),
        baseline_passes=sum(r.passed for r in base),
        errors=len(errored),
        fired_runs=fired,
        mean_score=_mean([r.score for r in runs if r.score is not None]),
        mean_cost_usd=_mean([r.cost_usd for r in runs]),
        mean_turns=_mean([float(r.turns) for r in runs if r.turns is not None]),
    )


def parse(
    raw: dict[str, Any], *, skill: str, version: str, model: str | None, policy: EvalPolicy
) -> SkillSummary:
    out = EvalOutput.model_validate(raw)
    notes = []
    if out.schema_version != SUPPORTED_SCHEMA:
        notes.append(f"claude plugin eval schemaVersion {out.schema_version} (sf expects 1)")
    if out.partial:
        notes.append("partial result: the cost ceiling was hit before all runs finished")
    return SkillSummary(
        skill=skill,
        version=version,
        model=model,
        claude_version=out.claude_version,
        cases=[summarise_case(c) for c in out.cases],
        total_cost_usd=out.cost_usd,
        partial=out.partial,
        policy=policy,
        notes=notes,
    )
