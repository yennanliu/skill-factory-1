"""Lint rules. Each rule is a function `(SkillDir) -> list[Finding]` registered with `@rule`.

Rule ids are stable (SFnnn) so they can be waived in spec.yaml `lint_waivers`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skill_factory.spec import SkillDir, spec_sha256

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    skill: str
    message: str
    hint: str = ""
    file: str = "SKILL.md"  # relative to the skill dir
    line: int | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: Severity
    summary: str
    check: Callable[[SkillDir], list[Finding]]


RULES: dict[str, Rule] = {}


def rule(
    rule_id: str, name: str, severity: Severity, summary: str
) -> Callable[[Callable[[SkillDir, Rule], Iterator[Finding]]], Rule]:
    def register(fn: Callable[[SkillDir, Rule], Iterator[Finding]]) -> Rule:
        def check(skill: SkillDir) -> list[Finding]:
            return list(fn(skill, r))

        r = Rule(rule_id, name, severity, summary, check)
        if rule_id in RULES:
            raise ValueError(f"duplicate rule id {rule_id}")
        RULES[rule_id] = r
        return r

    return register


def finding(r: Rule, skill: SkillDir, message: str, hint: str = "", **kw: object) -> Finding:
    return Finding(r.id, r.severity, skill.name, message, hint, **kw)  # type: ignore[arg-type]


# --- helpers -----------------------------------------------------------------------------

# Fences may be indented (e.g. a code block inside a numbered list item).
FENCE_RE = re.compile(r"^[ \t]{0,4}```.*?^[ \t]{0,4}```", re.S | re.M)
NUMBERED_RE = re.compile(r"^(?:#{2,4}\s+(?:Step\s+)?\d+[.)]|\s{0,3}\d+[.)]\s)", re.M)
VERIFY_RE = re.compile(
    r"\b(verify|verif|confirm|check|ensure|assert|make sure|expect|smoke[- ]test|validate)",
    re.I,
)
TABLE_HEADER_RE = re.compile(r"^\|(.+)\|\s*\n\|[\s:|-]+\|\s*$", re.M)
RECOVERY_WORDS = re.compile(
    r"\b(error|symptom|failure|fails?|problem|if .* goes wrong|recover)", re.I
)
TMP_RE = re.compile(r"(?<![\w$])/tmp/[\w.\-]+")
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "hard-coded credential",
        re.compile(
            r"""(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*["'](?!<|\$\{|your|xxx|changeme|example)[^"'\s]{8,}["']"""
        ),
    ),
]
TEXT_SUFFIXES = {".md", ".txt", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}


def _strip_fences(body: str) -> str:
    return FENCE_RE.sub("", body)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _skill_files(skill: SkillDir) -> Iterator[Path]:
    skip = {"evals", "fixtures", "__pycache__"}
    for p in sorted(skill.path.rglob("*")):
        if p.is_file() and not (set(p.relative_to(skill.path).parts) & skip):
            yield p


def _steps(body: str) -> list[str]:
    """Split the body (outside code fences) into numbered steps."""
    prose = _strip_fences(body)
    starts = [m.start() for m in NUMBERED_RE.finditer(prose)]
    if not starts:
        return []
    return [prose[a:b] for a, b in zip(starts, [*starts[1:], len(prose)], strict=True)]


# --- rules -------------------------------------------------------------------------------


@rule("SF001", "description-when", "warning", "description says when to use the skill")
def _description_when(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    desc = str(skill.frontmatter.get("description", ""))
    if not re.search(r"\b(use (it |this )?when|use for|use if|when (asked|the user))", desc, re.I):
        yield finding(
            r,
            skill,
            "description does not say when to use the skill",
            "add a 'Use when …' clause; the description is all the agent sees before loading",
            line=1,
        )


@rule("SF002", "description-length", "warning", "description fits the ~100-token always-on budget")
def _description_length(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    desc = str(skill.frontmatter.get("description", ""))
    approx_tokens = len(desc) // 4
    if approx_tokens > 100:
        yield finding(
            r,
            skill,
            f"description is ~{approx_tokens} tokens (budget ~100)",
            "it is loaded into every session; move detail into the body",
            line=1,
        )


@rule("SF003", "body-length", "error", "body is within spec.budget.max_body_lines")
def _body_length(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    n = len(skill.body.splitlines())
    limit = skill.spec.budget.max_body_lines
    if n > limit:
        yield finding(
            r,
            skill,
            f"body has {n} lines (limit {limit})",
            "move detail into references/ files that load on demand",
        )


@rule("SF004", "numbered-steps", "warning", "instructions are numbered steps")
def _numbered_steps(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    if len(_steps(skill.body)) < 2:
        yield finding(
            r,
            skill,
            "no numbered steps found",
            "write the workflow as numbered steps, one objective each",
        )


@rule("SF005", "step-verification", "warning", "most steps say how to verify them")
def _step_verification(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    steps = _steps(skill.body)
    if len(steps) < 2:
        return
    unverified = [s for s in steps if not VERIFY_RE.search(s)]
    if len(unverified) > len(steps) / 2:
        yield finding(
            r,
            skill,
            f"{len(unverified)}/{len(steps)} steps have no verification",
            "end each step with a check: 'confirm X exists', 'check the exit code', …",
        )


@rule("SF006", "concrete-commands", "warning", "skills that use Bash show concrete commands")
def _concrete_commands(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    uses_bash = "Bash" in skill.spec.tools.required or "Bash" in str(
        skill.frontmatter.get("allowed-tools", "")
    )
    if uses_bash and not FENCE_RE.search(skill.body):
        yield finding(
            r,
            skill,
            "skill uses Bash but shows no command block",
            "put the exact command in a fenced block instead of 'run the tests'",
        )


@rule("SF007", "error-recovery", "warning", "has an error-recovery table")
def _error_recovery(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    prose = _strip_fences(skill.body)
    if not any(RECOVERY_WORDS.search(h) for h in TABLE_HEADER_RE.findall(prose)):
        yield finding(
            r,
            skill,
            "no error-recovery table",
            "add a table like | Symptom | Action | for the failures you expect",
        )


@rule("SF008", "no-mcp-json", "error", "no .mcp.json inside the skill")
def _no_mcp_json(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    for p in skill.path.rglob(".mcp.json"):
        yield finding(
            r,
            skill,
            ".mcp.json found",
            "MCP tool definitions cost 50-70k tokens per turn; call the tool from a script instead",
            file=str(p.relative_to(skill.path)),
        )


@rule("SF009", "no-secrets", "error", "no credentials or keys in skill files")
def _no_secrets(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    for p in _skill_files(skill):
        if p.suffix not in TEXT_SUFFIXES:
            continue
        text = p.read_text(errors="replace")
        for label, pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                yield finding(
                    r,
                    skill,
                    f"possible {label}",
                    "remove it; read credentials from the environment or a keychain",
                    file=str(p.relative_to(skill.path)),
                    line=_line_of(text, m.start()),
                )


@rule("SF010", "no-shared-tmp", "error", "no fixed /tmp paths (concurrent sessions collide)")
def _no_shared_tmp(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    for p in _skill_files(skill):
        if p.suffix not in TEXT_SUFFIXES:
            continue
        text = p.read_text(errors="replace")
        for m in TMP_RE.finditer(text):
            yield finding(
                r,
                skill,
                f"fixed temp path {m.group(0)}",
                "use mktemp so parallel sessions don't overwrite each other",
                file=str(p.relative_to(skill.path)),
                line=_line_of(text, m.start()),
            )


@rule("SF011", "scripts-runnable", "error", "referenced scripts exist and are executable")
def _scripts_runnable(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    # Scripts the skill runs: everything in spec tools.scripts, plus paths used inside code blocks.
    # Prose mentions (e.g. another skill's script given as an example) are not checked.
    referenced = set(skill.spec.tools.scripts)
    for block in FENCE_RE.findall(skill.body):
        referenced |= set(re.findall(r"(?<![\w/])(scripts/[\w./\-]+\.(?:py|sh|js|ts))", block))
    for rel in sorted(referenced):
        p = skill.path / rel
        if not p.is_file():
            yield finding(r, skill, f"{rel} is referenced but missing", "add it or fix the path")
        elif p.suffix == ".sh" and not os.access(p, os.X_OK):
            yield finding(r, skill, f"{rel} is not executable", f"chmod +x {rel}", file=rel)


@rule("SF012", "spec-hash", "warning", "SKILL.md was compiled from the current spec")
def _spec_hash(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    m = re.search(r"sf:spec-sha256=([0-9a-f]{64})", skill.skill_md.read_text())
    if m and m.group(1) != spec_sha256(skill.spec_path):
        yield finding(
            r,
            skill,
            "spec.yaml changed since SKILL.md was compiled",
            "re-run `sf compile` (or update the marker after reviewing by hand)",
        )


@rule(
    "SF013",
    "external-effects-mocked",
    "error",
    "skills with external side effects have a mocked eval",
)
def _external_mocked(skill: SkillDir, r: Rule) -> Iterator[Finding]:
    if skill.spec.side_effects != "external":
        return
    evals = skill.path / "evals"
    cases = list(evals.rglob("*.yaml")) if evals.is_dir() else []
    if not any(re.search(r"^\s*(mocks|dry_run)\s*:", c.read_text(), re.M) for c in cases):
        yield finding(
            r,
            skill,
            "side_effects: external but no eval case uses mocks or dry_run",
            "evals must never hit the real external system; add a mocked case",
            file="spec.yaml",
        )
