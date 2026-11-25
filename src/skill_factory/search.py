"""`sf search`: find repo skills for a task by text match, weighted by eval quality."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from skill_factory.report.data import latest_eval
from skill_factory.spec import SkillDir

STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "use",
    "when",
    "asked",
    "into",
    "from",
    "your",
    "you",
    "are",
    "how",
    "what",
    "can",
    "should",
    "want",
    "need",
    "help",
    "skill",
}
WEIGHTS = {
    "name": 3.0,
    "triggers": 2.0,
    "goal": 2.0,
    "description": 1.0,
    "outputs": 1.0,
    "domain": 1.0,
}


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = set()
    for w in words:
        if len(w) >= 3 and w not in STOP:
            out.add(w)
            out.add(w.rstrip("s"))  # crude singular: tests ~ test
    return out


@dataclass(frozen=True)
class Hit:
    skill: str
    plugin: str
    tier: str
    score: float
    text_score: float
    quality: float
    goal: str
    install: str


def fields(s: SkillDir) -> dict[str, str]:
    return {
        "name": s.name.replace("-", " "),
        "triggers": " ".join(s.spec.triggers),
        "goal": s.spec.goal,
        "description": str(s.frontmatter.get("description", "")),
        "outputs": " ".join(s.spec.outputs),
        "domain": s.spec.domain or "",
    }


def quality(runs_dir: Path, skill: str) -> float:
    """Latest eval pass rate, halved when the skill shows no lift over the baseline; 0.5 if none."""
    e = latest_eval(runs_dir, skill)
    if e is None or e.pass_rate is None:
        return 0.5
    return e.pass_rate * (0.5 if e.lift is not None and e.lift <= 0 else 1.0)


def search(
    query: str, skills: list[SkillDir], runs_dir: Path, market: str, limit: int = 5
) -> list[Hit]:
    q = tokens(query)
    if not q:
        return []
    hits = []
    for s in skills:
        f = {k: tokens(v) for k, v in fields(s).items()}
        raw = sum(WEIGHTS[k] * len(q & toks) for k, toks in f.items())
        text = raw / (len(q) * sum(WEIGHTS.values()))
        if raw == 0:
            continue
        qual = quality(runs_dir, s.name)
        hits.append(
            Hit(
                skill=s.name,
                plugin=s.spec.plugin_name,
                tier=s.spec.tier,
                score=round(text * (0.5 + 0.5 * qual), 4),
                text_score=round(text, 4),
                quality=round(qual, 3),
                goal=s.spec.goal.strip(),
                install=f"/plugin install {s.spec.plugin_name}@{market}",
            )
        )
    return sorted(hits, key=lambda h: (-h.score, h.skill))[:limit]
