"""Judge calibration: how often does an LLM judge agree with the skill author?

Calibration items live in skills/<name>/evals/_calibration/*.yaml:

    - id: names-inner-loop
      criteria: The response names the nested inner loop as the O(n^2) bottleneck.
      response: |
        ...a response to grade...
      label: pass        # the author's own grade: pass | fail

`sf judge calibrate` grades every item with the judge (majority of N votes) and reports raw
agreement and Cohen's kappa. Below kappa 0.6 the judge's verdicts on that skill are not trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from skill_factory.compile.backend import Backend

KAPPA_TRUST = 0.6
JUDGE_SYSTEM = (
    "You grade one response against one criterion. Answer PASS only if the response clearly "
    "satisfies the criterion; otherwise FAIL. Judge only the criterion, not style."
)
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Item:
    id: str
    criteria: str
    response: str
    label: bool  # author says pass


@dataclass(frozen=True)
class Graded:
    item: Item
    judge: bool
    votes: list[bool]
    reasons: list[str]

    @property
    def agrees(self) -> bool:
        return self.judge == self.item.label

    @property
    def majority_reason(self) -> str:
        """A reason from a vote that agrees with the final verdict."""
        return next(
            (r for v, r in zip(self.votes, self.reasons, strict=True) if v == self.judge),
            self.reasons[0] if self.reasons else "",
        )


def load_items(evals_dir: Path) -> list[Item]:
    items = []
    for f in sorted((evals_dir / "_calibration").glob("*.yaml")):
        for raw in YAML(typ="safe").load(f.read_text()) or []:
            label = str(raw["label"]).lower()
            if label not in ("pass", "fail"):
                raise ValueError(f"{f.name}:{raw.get('id')}: label must be pass or fail")
            items.append(
                Item(str(raw["id"]), str(raw["criteria"]), str(raw["response"]), label == "pass")
            )
    return items


def grade(item: Item, backend: Backend, model: str, votes: int = 3) -> Graded:
    user = (
        f"Criterion:\n{item.criteria}\n\n"
        f"Response to grade:\n<response>\n{item.response}\n</response>"
    )
    verdicts, reasons = [], []
    for _ in range(votes):
        ans = backend.generate(JUDGE_SYSTEM, user, JUDGE_SCHEMA, model).data
        verdicts.append(ans["verdict"] == "PASS")
        reasons.append(str(ans.get("reason", "")))
    if not verdicts:
        raise ValueError("votes must be >= 1")
    # PASS only on a strict majority: a tie means the criterion isn't clearly met.
    majority = sum(verdicts) > len(verdicts) / 2
    return Graded(item, majority, verdicts, reasons)


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Kappa for two raters on binary labels; None when chance agreement is total."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)
