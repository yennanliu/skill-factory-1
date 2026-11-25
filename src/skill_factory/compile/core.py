"""Compile a spec into SKILL.md + draft eval cases, lint the result, retry with feedback."""

from __future__ import annotations

import difflib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from skill_factory.compile.backend import Backend
from skill_factory.lint import RULES, lint_skill
from skill_factory.lint.rules import Finding, _steps
from skill_factory.market.build import MARKER_RE
from skill_factory.spec import SkillDir, discover, load_skill, spec_sha256

MAX_EXEMPLAR_LINES = 220
MAX_SCRIPT_LINES = 60

GRADER = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": ["regex", "tool_used", "file_exists", "llm"]},
        "criteria": {"type": "string"},
        "pattern": {"type": "string"},
        "flags": {"type": "string"},
        "tool": {"type": "string"},
        "path": {"type": "string"},
        "min": {"type": "integer"},
        "max": {"type": "integer"},
    },
    "required": ["name", "type"],
    "additionalProperties": False,
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "body": {"type": "string"},
        "eval_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "prompt": {"type": "string"},
                    "graders": {"type": "array", "items": GRADER},
                },
                "required": ["name", "description", "prompt", "graders"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["description", "body", "eval_cases"],
    "additionalProperties": False,
}

SYSTEM = """\
You write Agent Skills (a SKILL.md an AI coding agent loads on demand) from a spec.

Return JSON with:
- description: one or two sentences, at most ~350 characters. Say what the skill does and, \
explicitly, when to use it ("Use when ..."), including the trigger phrases from the spec. It is \
loaded into every session, so every word must earn its place.
- body: the SKILL.md body in Markdown, without frontmatter. Start with a "# <Title>" line. \
Write the workflow as numbered steps, one objective per step. End every step with how to verify \
it. Put exact commands in fenced code blocks. Include an error-recovery table \
(| Symptom | Action |). Respect tools.forbidden. Keep it well under the line budget; move \
long reference material out of the body.
- eval_cases: 3 to 5 draft eval cases for Claude Code's `claude plugin eval` harness. Each has \
a kebab-case name, a description, a realistic user prompt (natural language, not a slash \
command), and graders. Every case runs in an EMPTY directory with no files, so the prompt must be \
self-contained: paste any code or text it is about into the prompt and never refer to a file. \
Grader types: regex (pattern in JavaScript regex syntax, checked against the final message; use \
the separate flags field, e.g. "i", never inline groups like (?i)), \
tool_used (tool, optional min/max; max 0 means "never used"), file_exists (path glob), llm \
(criteria). Include a `tool_used` grader with tool "Skill" in each case, and at least one \
negative check (a forbidden tool or action with max 0) when the spec forbids something.

Follow the house style of the example skills. Do not invent tools, scripts or files the spec \
does not mention. Any path that starts with scripts/ is checked to exist in THIS skill, so never \
use scripts/... paths in examples about other skills; call the skill's own scripts with the exact \
flags and values their usage text shows."""


@dataclass
class CompileResult:
    skill: str
    skill_md: str
    eval_cases: list[dict[str, Any]]
    findings: list[Finding]  # remaining (unwaived) lint findings after the last attempt
    attempts: int
    cost_usd: float | None
    notes: list[str] = field(default_factory=list)


def lint_checklist() -> str:
    return "\n".join(f"- {r.id} {r.summary}" for r in RULES.values())


def pick_exemplars(root: Path, exclude: str, n: int = 2) -> list[SkillDir]:
    """The n repo skills with the fewest lint waivers (ties by name), excluding the target."""
    candidates = []
    for path in discover(root):
        if path.name == exclude:
            continue
        try:
            candidates.append(load_skill(path))
        except Exception:
            continue
    # an uncompiled stub (`sf new`) or a body with no numbered steps is not house style
    candidates = [s for s in candidates if "Not compiled yet" not in s.body and _steps(s.body)]
    candidates.sort(key=lambda s: (len(s.spec.lint_waivers), s.name))
    return candidates[:n]


def build_prompt(skill: SkillDir, exemplars: list[SkillDir], feedback: list[str]) -> str:
    parts = [
        "## Spec (spec.yaml)\n```yaml\n" + skill.spec_path.read_text() + "```",
        "## Lint rules the result must pass\n" + lint_checklist(),
    ]
    for rel in skill.spec.tools.scripts:
        script = skill.path / rel
        if script.is_file():
            head = "\n".join(script.read_text().splitlines()[:MAX_SCRIPT_LINES])
            parts.append(
                f"## Script {rel} (use exactly this interface: flag names and allowed values)\n"
                f"```\n{head}\n```"
            )
    for ex in exemplars:
        lines = ex.skill_md.read_text().splitlines()
        text = "\n".join(lines[:MAX_EXEMPLAR_LINES])
        if len(lines) > MAX_EXEMPLAR_LINES:
            text += "\n… (truncated)"
        parts.append(f"## Example skill: {ex.name}\n````markdown\n{text}\n````")
    if feedback:
        parts.append(
            "## Your previous attempt failed these lint checks; fix all of them\n"
            + "\n".join(f"- {f}" for f in feedback)
        )
    parts.append("Write the skill for the spec above.")
    return "\n\n".join(parts)


def render_skill_md(skill: SkillDir, description: str, body: str) -> str:
    """Frontmatter from the spec (+ allowed-tools) + body + spec-hash marker."""
    yaml = YAML()
    yaml.width = 4096
    # Keep every existing frontmatter key (argument-hint, disable-model-invocation, ...);
    # only name and description are compiled.
    fm: dict[str, Any] = {**skill.frontmatter}
    fm["name"] = skill.spec.name
    fm["description"] = " ".join(description.split())
    tools = skill.frontmatter.get("allowed-tools") or ", ".join(
        t for t in skill.spec.tools.required if t not in skill.spec.tools.forbidden
    )
    if tools:
        fm["allowed-tools"] = tools
    from io import StringIO

    buf = StringIO()
    yaml.dump(fm, buf)
    body = MARKER_RE.sub("", body.strip())
    marker = f"<!-- sf:spec-sha256={spec_sha256(skill.spec_path)} -->"
    return f"---\n{buf.getvalue()}---\n\n{body}\n\n{marker}\n"


def _lint_candidate(skill: SkillDir, skill_md: str) -> list[Finding]:
    with tempfile.TemporaryDirectory() as tmp:
        cand = Path(tmp) / skill.name
        # evals/ stays in: SF013 (external side effects need a mocked eval) reads it
        shutil.copytree(skill.path, cand, ignore=shutil.ignore_patterns("SKILL.md.new"))
        (cand / "SKILL.md").write_text(skill_md)
        return lint_skill(cand).findings


def compile_skill(
    root: Path, skill: SkillDir, backend: Backend, model: str, max_retries: int = 2
) -> CompileResult:
    exemplars = pick_exemplars(root, skill.name)
    feedback: list[str] = []
    total_cost: float | None = 0.0
    skill_md, cases, findings = "", [], []
    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        answer = backend.generate(SYSTEM, build_prompt(skill, exemplars, feedback), SCHEMA, model)
        if total_cost is not None:
            total_cost = None if answer.cost_usd is None else total_cost + answer.cost_usd
        skill_md = render_skill_md(skill, answer.data["description"], answer.data["body"])
        cases = list(answer.data.get("eval_cases", []))
        findings = _lint_candidate(skill, skill_md)
        if not findings:
            break
        feedback = [
            f"{f.rule} ({f.severity}) {f.message}" + (f" — {f.hint}" if f.hint else "")
            for f in findings
        ]
    return CompileResult(skill.name, skill_md, cases, findings, attempts, total_cost)


def case_yaml(case: dict[str, Any], tools: list[str] | None = None) -> str:
    graders = []
    for g in case["graders"]:
        entry = {k: v for k, v in g.items() if v not in (None, "")}
        if entry.get("type") == "file_exists" and entry.get("max") == 0:
            entry["exists"] = False  # "no such file may be created", in the harness's terms
        # The harness rejects keys a grader type doesn't take (e.g. max on an llm grader).
        allowed = GRADER_KEYS.get(str(entry.get("type")), set())
        entry = {k: v for k, v in entry.items() if k in allowed | {"name", "type"}}
        if entry.get("type") == "regex" and "pattern" in entry:
            entry["pattern"], entry["flags"] = js_regex(entry["pattern"], entry.get("flags", ""))
            if not entry["flags"]:
                del entry["flags"]
        if entry.get("type") == "tool_used" and entry.get("max") == 0:
            entry["min"] = 0  # the harness defaults min to 1, which would make "never" unpassable
        graders.append(entry)
    doc = {
        "schema_version": "1.1",
        "name": case["name"],
        "description": case["description"],
        "tags": ["draft"],
        "execution": {
            "prompt": case["prompt"],
            "max_turns": 20,
            # read-only tools + Skill; gated tools (Bash/Write/...) are granted by `sf eval`
            # from the spec's tools.required
            "allowed_tools": list(dict.fromkeys(["Read", "Glob", "Grep", *(tools or []), "Skill"])),
        },
        "graders": graders,
    }
    yaml = YAML()
    yaml.width = 100
    from io import StringIO

    buf = StringIO()
    buf.write(
        "# Draft generated by `sf compile`. Review the prompt and graders before trusting it.\n"
    )
    yaml.dump(doc, buf)
    return buf.getvalue()


GRADER_KEYS = {
    "regex": {"pattern", "flags", "match", "target", "weight"},
    "tool_used": {"tool", "input_match", "min", "max", "weight"},
    "file_exists": {"path", "exists", "weight"},
    "llm": {"criteria", "focus", "weight"},
}
INLINE_FLAGS = re.compile(r"^\(\?([imsx]+)\)")


def js_regex(pattern: str, flags: str) -> tuple[str, str]:
    """The harness uses JavaScript regexes: move a leading inline group like (?i) into flags."""
    m = INLINE_FLAGS.match(pattern)
    if not m:
        return pattern, flags
    inline = m.group(1)
    rest = pattern[m.end() :]
    if "x" in inline:  # JS has no verbose flag: drop the whitespace and comments it ignored
        rest = strip_verbose(rest)
    extra = "".join(f for f in inline if f in "ims" and f not in flags)
    return rest, flags + extra


def strip_verbose(pattern: str) -> str:
    """Remove unescaped whitespace and #-comments outside character classes (re.X semantics)."""
    out: list[str] = []
    i, in_class = 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
            out.append(c)
        elif c == "[":
            in_class = True
            out.append(c)
        elif c == "#":
            while i < len(pattern) and pattern[i] != "\n":
                i += 1
            continue
        elif not c.isspace():
            out.append(c)
        i += 1
    return "".join(out)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "case"


def write_outputs(skill: SkillDir, result: CompileResult, apply: bool) -> tuple[Path, list[Path]]:
    """Write SKILL.md (or SKILL.md.new) and draft eval cases that don't exist yet."""
    target = skill.skill_md if apply else skill.path / "SKILL.md.new"
    target.write_text(result.skill_md)
    if apply:
        (skill.path / "SKILL.md.new").unlink(missing_ok=True)
    written = []
    for case in result.eval_cases:
        case_dir = skill.path / "evals" / slug(case["name"])
        if case_dir.exists():
            continue  # never overwrite a reviewed case
        case_dir.mkdir(parents=True)
        tools = [t for t in skill.spec.tools.required if t not in skill.spec.tools.forbidden]
        (case_dir / "case.yaml").write_text(case_yaml({**case, "name": slug(case["name"])}, tools))
        written.append(case_dir / "case.yaml")
    return target, written


def diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=f"{path} (compiled)",
        )
    )


def stale_skills(root: Path) -> list[tuple[str, str]]:
    """(skill, reason) for compiled skills whose spec changed since compiling."""
    out = []
    for path in discover(root):
        md = path / "SKILL.md"
        spec = path / "spec.yaml"
        if not md.is_file() or not spec.is_file():
            continue
        m = re.search(r"sf:spec-sha256=([0-9a-f]{64})", md.read_text())
        if m and m.group(1) != spec_sha256(spec):
            out.append((path.name, "spec.yaml changed since SKILL.md was compiled"))
    return out
