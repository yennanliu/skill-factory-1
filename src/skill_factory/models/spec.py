"""`skills/<name>/spec.yaml` — the human-written source of truth for a skill."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SEMVER_RE = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
KEBAB_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"

SemVer = Annotated[str, StringConstraints(pattern=SEMVER_RE)]
Kebab = Annotated[str, StringConstraints(pattern=KEBAB_RE)]
Tier = Literal["always-on", "core", "domain", "experimental"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tools(_Strict):
    required: list[str] = []
    forbidden: list[str] = []
    scripts: list[str] = []  # paths relative to the skill dir


class Targets(_Strict):
    agents: list[Literal["claude-code", "codex", "opencode", "gemini-cli"]] = ["claude-code"]
    models: list[str] = []  # first entry = default model for evals


class Budget(_Strict):
    max_body_lines: int = Field(500, gt=0)
    target_cost_usd: float | None = Field(None, gt=0)


class EvalPolicy(_Strict):
    min_pass_rate: float = Field(0.7, ge=0, le=1)
    max_pass_rate: float = Field(0.95, ge=0, le=1)
    runs: int = Field(5, gt=0)
    # Extra tool grants for evals, e.g. Write/Edit for a read-only skill so a "never edits"
    # grader can actually fail. Not granted to the skill itself.
    allow_tools: list[str] = []

    @model_validator(mode="after")
    def _ordered(self) -> EvalPolicy:
        if self.min_pass_rate > self.max_pass_rate:
            raise ValueError("min_pass_rate must be <= max_pass_rate")
        return self


class Spec(_Strict):
    name: Kebab
    version: SemVer
    tier: Tier
    domain: Kebab | None = None
    owner: str
    goal: str
    triggers: list[str] = Field(min_length=1)
    outputs: list[str] = []
    tools: Tools = Tools()
    targets: Targets = Targets()
    budget: Budget = Budget()
    evals: EvalPolicy = EvalPolicy()
    side_effects: Literal["none", "local", "external"] = "none"
    # true: `sf build` writes references/catalog.json (all marketplace skills) into this skill
    catalog: bool = False
    # Rule ids (e.g. "SF007") waived for this skill; add a YAML comment with the reason.
    lint_waivers: list[str] = []

    @model_validator(mode="after")
    def _domain_matches_tier(self) -> Spec:
        if self.tier == "domain" and not self.domain:
            raise ValueError("tier 'domain' requires a 'domain' field")
        if self.tier != "domain" and self.domain:
            raise ValueError(f"'domain' is only allowed with tier 'domain' (tier is {self.tier!r})")
        return self

    @property
    def plugin_name(self) -> str:
        return plugin_for(self.tier, self.domain)


def plugin_for(tier: Tier, domain: str | None) -> str:
    """Map a tier (and domain) to the generated plugin that ships the skill."""
    if tier == "always-on":
        return "sf-platform"
    if tier == "domain":
        assert domain, "domain tier needs a domain"
        return f"sf-domain-{domain}"
    return f"sf-{tier}"


def bump(version: str, part: Literal["major", "minor", "patch"]) -> str:
    m = re.match(SEMVER_RE, version)
    if not m:
        raise ValueError(f"not a semver: {version!r}")
    major, minor, patch = (int(g) for g in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
