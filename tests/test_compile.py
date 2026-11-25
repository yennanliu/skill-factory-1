from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from skill_factory.compile import backend as backend_mod
from skill_factory.compile.backend import Answer, BackendError, ClaudeCliBackend
from skill_factory.compile.core import (
    SCHEMA,
    build_prompt,
    case_yaml,
    compile_skill,
    js_regex,
    pick_exemplars,
    render_skill_md,
    stale_skills,
    write_outputs,
)
from skill_factory.compile.new import NewSkillError, new_skill
from skill_factory.lint import lint_skill
from skill_factory.market import build
from skill_factory.spec import load_skill, spec_sha256

GOOD_BODY = """\
# Alpha

1. Run the build and check the exit code is 0.
   ```bash
   make build
   ```
2. Confirm `out/alpha.txt` exists.

## Recovery

| Symptom | Action |
|---|---|
| build fails | read the log, fix, re-run step 1 |
"""
CASE = {
    "name": "Does Alpha",
    "description": "d",
    "prompt": "please do alpha",
    "graders": [
        {"name": "fired", "type": "tool_used", "tool": "Skill"},
        {"name": "no-bash", "type": "tool_used", "tool": "Bash", "max": 0},
        {"name": "said", "type": "regex", "pattern": "(?i)alpha", "criteria": ""},
    ],
}


class FakeBackend:
    name = "fake"

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def generate(self, system: str, user: str, schema: dict[str, Any], model: str) -> Answer:
        assert schema is SCHEMA and "Use when" in system
        self.prompts.append(user)
        return Answer(self.answers.pop(0), 0.05)


def answer(
    body: str = GOOD_BODY, desc: str = "Does alpha. Use when asked to do alpha."
) -> dict[str, Any]:
    return {"description": desc, "body": body, "eval_cases": [CASE]}


def test_compile_succeeds_first_try(repo: Path) -> None:
    skill = load_skill(repo / "skills" / "alpha")
    fake = FakeBackend([answer()])
    result = compile_skill(repo, skill, fake, "m")
    assert result.attempts == 1 and result.findings == [] and result.cost_usd == pytest.approx(0.05)
    assert result.skill_md.startswith("---\nname: alpha\ndescription: Does alpha. Use when")
    assert result.skill_md.rstrip().endswith(
        f"<!-- sf:spec-sha256={spec_sha256(skill.spec_path)} -->"
    )
    assert "## Spec (spec.yaml)" in fake.prompts[0] and "SF007" in fake.prompts[0]
    assert "## Example skill: beta" in fake.prompts[0]  # the other repo skill is the exemplar


def test_compile_feeds_lint_failures_back(repo: Path) -> None:
    skill = load_skill(repo / "skills" / "alpha")
    bad = answer(body=GOOD_BODY.split("## Recovery")[0])  # no recovery table -> SF007
    fake = FakeBackend([bad, answer()])
    result = compile_skill(repo, skill, fake, "m")
    assert result.attempts == 2 and result.findings == []
    assert "SF007" in fake.prompts[1] and "previous attempt failed" in fake.prompts[1]
    assert result.cost_usd == pytest.approx(0.10)


def test_compile_gives_up_after_retries(repo: Path) -> None:
    skill = load_skill(repo / "skills" / "alpha")
    bad = answer(desc="Does alpha.")  # no "Use when" -> SF001
    result = compile_skill(repo, skill, FakeBackend([bad, bad, bad]), "m", max_retries=2)
    assert result.attempts == 3
    assert [f.rule for f in result.findings] == ["SF001"]


def test_compiled_skill_is_lint_clean_and_build_replaces_marker(repo: Path) -> None:
    skill = load_skill(repo / "skills" / "alpha")
    result = compile_skill(repo, skill, FakeBackend([answer()]), "m")
    write_outputs(skill, result, apply=True)
    assert lint_skill(repo / "skills" / "alpha").findings == []
    build(repo)
    built = (repo / "plugins" / "sf-core" / "skills" / "alpha" / "SKILL.md").read_text()
    assert built.count("sf:spec-sha256=") == 1 and "sf:version=1.0.0" in built
    spec = repo / "skills" / "alpha" / "spec.yaml"
    assert stale_skills(repo) == []
    spec.write_text(spec.read_text() + "outputs: [x]\n")
    assert stale_skills(repo) == [("alpha", "spec.yaml changed since SKILL.md was compiled")]


def test_write_outputs_without_apply_and_never_overwrites_cases(repo: Path) -> None:
    skill = load_skill(repo / "skills" / "alpha")
    before = skill.skill_md.read_text()
    result = compile_skill(repo, skill, FakeBackend([answer()]), "m")
    target, cases = write_outputs(skill, result, apply=False)
    assert target.name == "SKILL.md.new" and skill.skill_md.read_text() == before
    assert [c.parent.name for c in cases] == ["does-alpha"]
    case_file = cases[0]
    case_file.write_text("# reviewed by a human\n")
    _, again = write_outputs(skill, result, apply=False)
    assert again == [] and case_file.read_text() == "# reviewed by a human\n"
    build(repo)
    assert not (repo / "plugins" / "sf-core" / "skills" / "alpha" / "SKILL.md.new").exists()


def test_case_yaml_fixes_never_graders_and_regex_flags() -> None:
    from ruamel.yaml import YAML

    doc = YAML(typ="safe").load(case_yaml({**CASE, "name": "does-alpha"}))
    assert doc["schema_version"] == "1.1" and doc["tags"] == ["draft"]
    graders = {g["name"]: g for g in doc["graders"]}
    assert graders["no-bash"] == {
        "name": "no-bash",
        "type": "tool_used",
        "tool": "Bash",
        "max": 0,
        "min": 0,
    }
    assert graders["said"]["pattern"] == "alpha" and graders["said"]["flags"] == "i"
    assert "criteria" not in graders["said"]  # empty strings dropped


@pytest.mark.parametrize(
    ("pattern", "flags", "expected"),
    [
        ("(?i)abc", "", ("abc", "i")),
        ("(?ix)a b", "g", ("ab", "gi")),
        ("abc", "", ("abc", "")),
        ("a(?i)b", "", ("a(?i)b", "")),
    ],
)
def test_js_regex(pattern: str, flags: str, expected: tuple[str, str]) -> None:
    assert js_regex(pattern, flags) == expected


def test_render_keeps_existing_allowed_tools(repo: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "tools:\n  required: [Read, Bash]\n")
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text().replace("description:", "allowed-tools: Read, Bash\ndescription:"))
    out = render_skill_md(load_skill(repo / "skills" / "alpha"), "d.\n  Use when x.", "# A\n")
    assert "allowed-tools: Read, Bash" in out and "description: d. Use when x." in out


def test_pick_exemplars_prefers_fewest_waivers(repo: Path) -> None:
    spec = repo / "skills" / "beta" / "spec.yaml"
    spec.write_text(spec.read_text() + "lint_waivers: [SF007]\n")
    from tests.conftest import make_skill

    make_skill(repo, "gamma")
    assert [s.name for s in pick_exemplars(repo, "alpha")] == ["gamma", "beta"]
    prompt = build_prompt(load_skill(repo / "skills" / "alpha"), [], ["SF001 (warning) x"])
    assert "- SF001 (warning) x" in prompt


def test_new_skill(repo: Path) -> None:
    path = new_skill(repo, "fresh-one", "domain", "games", None, "Play games.")
    s = load_skill(path)
    assert (s.spec.tier, s.spec.domain, s.spec.owner, s.spec.plugin_name) == (
        "domain",
        "games",
        "tester",
        "sf-domain-games",
    )
    assert "sf-domain-games:" in (repo / "market.yaml").read_text()
    assert "# marketplace for tests" in (repo / "market.yaml").read_text()  # comments kept
    assert (path / "evals" / "smoke" / "case.yaml").is_file()
    build(repo)  # the new plugin entry builds


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        (("Bad", "core", None), "kebab-case"),
        (("x", "domain", None), "needs --domain"),
        (("x", "core", "d"), "only valid with --tier domain"),
        (("alpha", "core", None), "already exists"),
    ],
)
def test_new_skill_errors(repo: Path, args: tuple[str, str, str | None], msg: str) -> None:
    with pytest.raises(NewSkillError, match=msg):
        new_skill(repo, *args, None, None)


def test_cli_backend_command_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    be = ClaudeCliBackend()
    cmd = be.command("sys", "user", {"type": "object"}, "claude-opus-5-5")
    joined = " ".join(cmd)
    assert cmd[:3] == ["claude", "-p", "user"] and "--tools  " in joined + " "
    for part in ("--json-schema", "--model claude-opus-5-5", "--no-session-persistence"):
        assert part in joined
    monkeypatch.setattr(backend_mod.shutil, "which", lambda _x: "/bin/claude")

    def fake_run(stdout: str) -> Any:
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        backend_mod.subprocess,
        "run",
        fake_run(
            json.dumps({"is_error": False, "structured_output": {"a": 1}, "total_cost_usd": 0.2})
        ),
    )
    assert be.generate("s", "u", {}, "m") == Answer({"a": 1}, 0.2)
    monkeypatch.setattr(
        backend_mod.subprocess,
        "run",
        fake_run(json.dumps({"is_error": True, "subtype": "error_max_turns", "result": "x"})),
    )
    with pytest.raises(BackendError, match="error_max_turns"):
        be.generate("s", "u", {}, "m")
    monkeypatch.setattr(backend_mod.subprocess, "run", fake_run("not json"))
    with pytest.raises(BackendError, match="no JSON"):
        be.generate("s", "u", {}, "m")


def test_get_backend() -> None:
    assert backend_mod.get_backend("claude-cli").name == "claude-cli"
    assert backend_mod.get_backend("api").name == "api"
    with pytest.raises(ValueError, match="unknown backend"):
        backend_mod.get_backend("nope")


def test_case_yaml_drops_keys_the_grader_type_does_not_take() -> None:
    from ruamel.yaml import YAML

    case = {
        **CASE,
        "name": "x",
        "graders": [
            {"name": "judge", "type": "llm", "criteria": "c", "max": 0, "tool": "Bash"},
            {"name": "never", "type": "tool_used", "tool": "Write", "max": 0, "criteria": "z"},
            {"name": "no-file", "type": "file_exists", "path": "**/SKILL.md", "max": 0, "min": 0},
        ],
    }
    graders = YAML(typ="safe").load(case_yaml(case))["graders"]
    assert graders[0] == {"name": "judge", "type": "llm", "criteria": "c"}
    assert graders[1] == {"name": "never", "type": "tool_used", "tool": "Write", "max": 0, "min": 0}
    assert graders[2] == {
        "name": "no-file",
        "type": "file_exists",
        "path": "**/SKILL.md",
        "exists": False,
    }


def test_release_does_not_make_a_compiled_skill_stale(repo: Path) -> None:
    from skill_factory.market import release

    skill = load_skill(repo / "skills" / "alpha")
    write_outputs(skill, compile_skill(repo, skill, FakeBackend([answer()]), "m"), apply=True)
    assert stale_skills(repo) == []
    release(repo, "alpha", "patch")
    assert stale_skills(repo) == []  # only the version changed
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "# just a comment\n")
    assert stale_skills(repo) == []  # comments don't count either
    spec.write_text(spec.read_text() + "outputs: [x]\n")
    assert stale_skills(repo) != []


def test_compile_lints_with_evals_so_sf013_can_pass(repo: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text().replace("side_effects", "x") + "side_effects: external\n")
    (repo / "skills" / "alpha" / "evals" / "m").mkdir(parents=True)
    (repo / "skills" / "alpha" / "evals" / "m" / "case.yaml").write_text("mocks: {}\n")
    result = compile_skill(
        repo, load_skill(repo / "skills" / "alpha"), FakeBackend([answer()]), "m"
    )
    assert result.findings == [] and result.attempts == 1


def test_render_keeps_other_frontmatter_keys(repo: Path) -> None:
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text().replace("description:", "argument-hint: <n>\ndescription:"))
    out = render_skill_md(load_skill(repo / "skills" / "alpha"), "d. Use when x.", "# A\n")
    assert "argument-hint: <n>" in out and "description: d. Use when x." in out


def test_exemplars_skip_uncompiled_stubs(repo: Path) -> None:
    new_skill(repo, "aaa-stub", "core", None, None, None)
    md = repo / "skills" / "beta" / "SKILL.md"
    md.write_text(md.read_text().replace("1. Step one.", "1. Step one.\n2. Step two."))
    names = [s.name for s in pick_exemplars(repo, "alpha")]
    assert "aaa-stub" not in names


def test_new_skill_quotes_values(repo: Path) -> None:
    path = new_skill(repo, "quoted", "core", None, "@jim", "line one\nline two: yes")
    s = load_skill(path)
    assert s.spec.owner == "@jim" and s.spec.goal == "line one line two: yes"


def test_draft_case_lists_spec_tools(repo: Path) -> None:
    from ruamel.yaml import YAML

    doc = YAML(typ="safe").load(case_yaml({**CASE, "name": "x"}, ["Bash", "Write"]))
    assert doc["execution"]["allowed_tools"] == ["Read", "Glob", "Grep", "Bash", "Write", "Skill"]


def test_backend_timeout_and_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic

    be = ClaudeCliBackend(timeout_seconds=1)
    monkeypatch.setattr(backend_mod.shutil, "which", lambda _x: "/bin/claude")

    def slow(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(a[0], 1)

    monkeypatch.setattr(backend_mod.subprocess, "run", slow)
    with pytest.raises(BackendError, match="timed out"):
        be.generate("s", "u", {}, "m")

    def no_creds(*a: Any, **k: Any) -> Any:
        raise anthropic.AnthropicError("no credentials")

    monkeypatch.setattr(anthropic, "Anthropic", no_creds)
    with pytest.raises(BackendError, match="backend claude-cli"):
        backend_mod.ApiBackend().generate("s", "u", {}, "m")
