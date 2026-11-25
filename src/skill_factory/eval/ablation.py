"""Ablation: does the skill (and each of its sections) earn its tokens? (design doc §7.3)

Arms: the full skill with a no-skill baseline (one `claude plugin eval` run in with-without
mode), then one single-arm run per variant with a `## ` section removed. Decisions:

| result                                   | verdict                 |
|------------------------------------------|-------------------------|
| lift > margin, cost <= 1.5x baseline     | keep                    |
| lift > margin, cost > 1.5x baseline      | optimize                |
| |lift| <= margin                         | shorten or retire       |
| lift < -margin                           | retire                  |
| removing a section drops <= margin       | delete section          |
| full skill above max_pass_rate           | inconclusive (too easy) |

Sections are removed one at a time, so two sections that cover for each other both look
deletable: delete one, then re-run the ablation before deleting another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

LIFT_MARGIN = 0.10  # pass-rate difference treated as noise at the run counts we can afford
COST_RATIO = 1.5

SECTION_RE = re.compile(r"^## +(.+?)\s*$", re.M)
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)


@dataclass(frozen=True)
class Section:
    title: str
    start: int  # offset in the SKILL.md text
    end: int

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:40] or "section"


def sections(skill_md: str) -> list[Section]:
    """Level-2 sections (`## Title`) outside code fences, each up to the next `## ` or the end."""
    fenced = [(m.start(), m.end()) for m in FENCE_RE.finditer(skill_md)]
    heads = [
        m for m in SECTION_RE.finditer(skill_md) if not any(a <= m.start() < b for a, b in fenced)
    ]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(skill_md)
        out.append(Section(m.group(1), m.start(), end))
    # a trailing build/compile marker belongs to no section
    return out


def without_section(skill_md: str, section: Section) -> str:
    tail = skill_md[section.end :]
    marker = re.search(r"\n*<!-- sf:[^>]*-->\s*$", skill_md[section.start : section.end])
    keep = marker.group(0) if marker else ""
    return skill_md[: section.start].rstrip("\n") + "\n\n" + tail.lstrip("\n") + keep


SkillVerdict = Literal["keep", "optimize", "shorten or retire", "retire", "no baseline"]
SectionVerdict = Literal["keep section", "delete section", "inconclusive (eval too easy)"]


def skill_verdict(
    pass_rate: float | None, baseline: float | None, cost: float | None, baseline_cost: float | None
) -> SkillVerdict:
    if pass_rate is None or baseline is None:
        return "no baseline"
    lift = pass_rate - baseline
    if lift > LIFT_MARGIN:
        if cost and baseline_cost and cost > COST_RATIO * baseline_cost:
            return "optimize"
        return "keep"
    if lift < -LIFT_MARGIN:
        return "retire"
    return "shorten or retire"


def section_verdict(
    full: float | None, without: float | None, max_pass_rate: float = 1.0
) -> SectionVerdict:
    if full is None or without is None:
        return "keep section"
    if full > max_pass_rate and without >= full - LIFT_MARGIN:
        # The suite can't show a drop from a ceiling it is already at.
        return "inconclusive (eval too easy)"
    return "delete section" if full - without <= LIFT_MARGIN else "keep section"


def report(full: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the ablation.json document from summary dicts (SkillSummary.to_json())."""
    max_rate = float((full.get("policy") or {}).get("max_pass_rate", 1.0))
    verdict = skill_verdict(
        full.get("pass_rate"),
        full.get("baseline_pass_rate"),
        full.get("mean_cost_usd"),
        full.get("baseline_mean_cost_usd"),
    )
    rows = []
    for v in variants:
        rows.append(
            {
                "section": v["section"],
                "pass_rate": v["summary"].get("pass_rate"),
                "delta": None
                if v["summary"].get("pass_rate") is None or full.get("pass_rate") is None
                else v["summary"]["pass_rate"] - full["pass_rate"],
                "tokens_saved": v["tokens_saved"],
                "verdict": section_verdict(
                    full.get("pass_rate"), v["summary"].get("pass_rate"), max_rate
                ),
            }
        )
    return {
        "skill": full["skill"],
        "model": full.get("model"),
        "verdict": verdict,
        "pass_rate": full.get("pass_rate"),
        "baseline_pass_rate": full.get("baseline_pass_rate"),
        "mean_cost_usd": full.get("mean_cost_usd"),
        "baseline_mean_cost_usd": full.get("baseline_mean_cost_usd"),
        "lift_margin": LIFT_MARGIN,
        "eval_too_easy": (full.get("pass_rate") or 0) > max_rate,
        "sections": rows,
    }
