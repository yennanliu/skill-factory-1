"""Model benchmark: run a skill's suite on several models, pick the cheapest that meets the bar."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skill_factory import yamlio
from skill_factory.eval.result import SkillSummary


@dataclass(frozen=True)
class BenchRow:
    model: str
    pass_rate: float | None
    mean_cost_usd: float | None
    fired: float | None  # share of runs where the Skill tool fired (cases that check it)
    meets_bar: bool
    partial: bool = False  # cost ceiling hit: too few runs to trust


def fired_rate(s: SkillSummary) -> float | None:
    checked = [c for c in s.cases if c.fired_runs is not None]
    n = sum(c.runs for c in checked)
    return sum(c.fired_runs or 0 for c in checked) / n if n else None


def rows(summaries: dict[str, SkillSummary], min_pass_rate: float) -> list[BenchRow]:
    return [
        BenchRow(
            m,
            s.pass_rate,
            s.mean_cost_usd,
            fired_rate(s),
            # a truncated (partial) sample never counts as meeting the bar
            s.pass_rate is not None and s.pass_rate >= min_pass_rate and not s.partial,
            s.partial,
        )
        for m, s in summaries.items()
    ]


def recommend(table: list[BenchRow]) -> str | None:
    """Cheapest model that meets the bar; if none does, the one with the best pass rate."""
    passing = [r for r in table if r.meets_bar and r.mean_cost_usd is not None]
    if passing:
        return min(passing, key=lambda r: r.mean_cost_usd or 0.0).model
    scored = [r for r in table if r.pass_rate is not None]
    return max(scored, key=lambda r: r.pass_rate or 0.0).model if scored else None


def write_default_model(spec_path: Path, model: str) -> list[str]:
    """Move `model` to the front of targets.models (adding it if missing).

    Edits the existing YAML sequence in place, so comments and flow style survive.
    """
    doc = yamlio.load(spec_path)
    if doc.get("targets") is None:
        doc["targets"] = {}
    targets = doc["targets"]
    if targets.get("models") is None:
        targets["models"] = []
    seq = targets["models"]
    while model in seq:
        seq.remove(model)
    seq.insert(0, model)
    yamlio.dump(doc, spec_path)
    return [str(m) for m in seq]
