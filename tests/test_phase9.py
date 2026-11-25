from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from skill_factory import agents, install
from skill_factory.market import build
from skill_factory.spec import load_skill

ROOT = Path(__file__).resolve().parents[1]


# --- sf install --------------------------------------------------------------------------


def test_target_dirs(tmp_path: Path) -> None:
    home, cwd = tmp_path / "h", tmp_path / "c"
    assert install.target_dir("codex", "user", home, cwd) == home / ".codex/skills"
    assert install.target_dir("opencode", "user", home, cwd) == home / ".config/opencode/skills"
    assert install.target_dir("gemini", "project", home, cwd) == cwd / ".gemini/skills"


def test_portable_frontmatter_drops_claude_only_keys() -> None:
    md = (
        "---\nname: a\ndescription: d. Use when x.\nallowed-tools: Read, Bash\nlicense: MIT\n"
        "---\n\n# A\n"
    )
    out = install.portable_skill_md(md)
    assert "allowed-tools" not in out
    assert out.startswith("---\nname: a\ndescription: d. Use when x.\nlicense: MIT\n---\n\n# A")


def test_plan_and_apply(repo: Path, tmp_path: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "targets:\n  agents: [claude-code, codex]\n")
    build(repo)
    skills = [load_skill(repo / "skills" / n) for n in ("alpha", "beta")]
    dest = tmp_path / "agent-skills"
    planned = install.plan(skills, repo, "codex", dest)
    assert [(p.skill, p.action) for p in planned] == [("alpha", "install"), ("beta", "skip-target")]
    for p in planned:
        install.apply(p, "codex")
    assert (dest / "alpha" / "SKILL.md").is_file() and (dest / "alpha" / install.MARKER).is_file()
    assert not (dest / "beta").exists()
    again = install.plan(skills, repo, "codex", dest, all_skills=True)
    assert [(p.skill, p.action) for p in again] == [("alpha", "update"), ("beta", "install")]


def test_never_overwrites_a_foreign_skill(repo: Path, tmp_path: Path) -> None:
    build(repo)
    dest = tmp_path / "agent-skills"
    (dest / "alpha").mkdir(parents=True)
    (dest / "alpha" / "SKILL.md").write_text("mine\n")
    (p,) = install.plan([load_skill(repo / "skills" / "alpha")], repo, "gemini", dest, True)
    assert p.action == "skip-foreign"
    install.apply(p, "gemini")
    assert (dest / "alpha" / "SKILL.md").read_text() == "mine\n"


def test_apply_requires_a_build(repo: Path, tmp_path: Path) -> None:
    (p,) = install.plan([load_skill(repo / "skills" / "alpha")], repo, "codex", tmp_path, True)
    with pytest.raises(FileNotFoundError, match="sf build"):
        install.apply(p, "codex")


# --- managed agents ----------------------------------------------------------------------

AGENT = """\
name: digest
plugins: [sf-core]
prompt: list the skills
model: m
outcome:
  contains: ["Needs attention"]
  contains_skill_names: true
  max_cost_usd: 0.1
"""


@pytest.fixture
def with_agent(repo: Path) -> Path:
    (repo / "managed").mkdir()
    (repo / "managed" / "digest.yaml").write_text(AGENT)
    return repo


def test_load_and_command(with_agent: Path) -> None:
    (a,) = agents.load(with_agent).values()
    cmd = agents.command(a, with_agent)
    joined = " ".join(cmd)
    assert cmd[:3] == ["claude", "-p", "list the skills"]
    for part in (
        "--model m",
        f"--plugin-dir {with_agent / 'plugins' / 'sf-core'}",
        "--allowedTools Read Glob Grep Skill",
        "--no-session-persistence",
    ):
        assert part in joined


def test_check_outcome(with_agent: Path) -> None:
    (a,) = agents.load(with_agent).values()
    good = agents.check_outcome(a, "alpha beta\nNeeds attention: none", 0.05, with_agent)
    assert all(good.values()) and len(good) == 4
    bad = agents.check_outcome(a, "alpha only", 0.5, with_agent)
    assert {k for k, v in bad.items() if not v} == {
        "contains 'Needs attention'",
        "mentions beta",
        "cost <= $0.1",
    }


def fake_claude(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    monkeypatch.setattr(agents.shutil, "which", lambda _x: "/bin/claude")
    monkeypatch.setattr(
        agents.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(result), stderr=""),
    )


def test_run_records_outcome(with_agent: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (a,) = agents.load(with_agent).values()
    fake_claude(
        monkeypatch,
        {"result": "alpha, beta. Needs attention: -", "total_cost_usd": 0.02, "num_turns": 4},
    )
    rec, path = agents.run(a, with_agent)
    assert rec.passed and rec.cost_usd == 0.02 and rec.turns == 4
    assert json.loads(path.read_text())["passed"] is True
    assert path.with_suffix(".md").read_text().startswith("alpha, beta")


def test_run_handles_errors(with_agent: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (a,) = agents.load(with_agent).values()
    fake_claude(monkeypatch, {"is_error": True, "subtype": "error_max_turns", "result": "x"})
    rec, _ = agents.run(a, with_agent)
    assert not rec.passed and rec.error and "error_max_turns" in rec.error
    monkeypatch.setattr(agents.shutil, "which", lambda _x: None)
    rec, _ = agents.run(a, with_agent)
    assert rec.error == "claude CLI not found"


def test_repo_agents_and_workflows_are_valid() -> None:
    defs = agents.load(ROOT)
    assert "weekly-skill-digest" in defs
    for a in defs.values():
        for p in a.plugins:
            assert (ROOT / "plugins" / p).is_dir(), p
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        doc = YAML(typ="safe").load(wf.read_text())
        assert doc["jobs"], wf.name
        text = wf.read_text()
        if "uv run" in text:  # any workflow that runs Python never resolves dependencies fresh
            assert "uv sync --locked" in text, wf.name


def test_agent_timeout_and_os_errors_still_write_a_record(
    with_agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (a,) = agents.load(with_agent).values()
    monkeypatch.setattr(agents.shutil, "which", lambda _x: "/bin/claude")

    def slow(*args: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(args[0], kw["timeout"])

    monkeypatch.setattr(agents.subprocess, "run", slow)
    rec, path = agents.run(a, with_agent, timeout=7)
    assert not rec.passed and rec.error and "timed out after 7s" in rec.error and path.is_file()

    def broken(*args: Any, **kw: Any) -> Any:
        raise OSError("exec format error")

    monkeypatch.setattr(agents.subprocess, "run", broken)
    rec, _ = agents.run(a, with_agent)
    assert rec.error and "exec format error" in rec.error


def test_agent_names_must_be_unique_and_match_files(with_agent: Path) -> None:
    (with_agent / "managed" / "other.yaml").write_text(AGENT)  # name: digest
    with pytest.raises(ValueError, match="must match the file name"):
        agents.load(with_agent)


def test_skill_names_match_whole_words(with_agent: Path) -> None:
    (a,) = agents.load(with_agent).values()
    checks = agents.check_outcome(a, "alphabet betamax Needs attention", 0.01, with_agent)
    assert checks["mentions alpha"] is False and checks["mentions beta"] is False


def test_install_is_atomic(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build(repo)
    dest = tmp_path / "agent-skills"
    skills = [load_skill(repo / "skills" / "alpha")]
    (p,) = install.plan(skills, repo, "codex", dest, True)
    install.apply(p, "codex")
    good = (dest / "alpha" / "SKILL.md").read_text()

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(install.shutil, "copytree", boom)
    (p,) = install.plan(skills, repo, "codex", dest, True)
    with pytest.raises(OSError, match="disk full"):
        install.apply(p, "codex")
    assert (dest / "alpha" / "SKILL.md").read_text() == good  # previous copy intact


def test_check_sources_before_any_change(repo: Path, tmp_path: Path) -> None:
    planned = install.plan([load_skill(repo / "skills" / "alpha")], repo, "codex", tmp_path, True)
    with pytest.raises(FileNotFoundError, match="sf build"):
        install.check_sources(planned)


def test_ci_comment_is_sticky_and_ablation_failures_surface() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--edit-last --create-if-none" in ci
    nightly = (ROOT / ".github" / "workflows" / "nightly-evals.yml").read_text()
    assert "|| true" not in nightly and "skills/*/evals" in nightly
