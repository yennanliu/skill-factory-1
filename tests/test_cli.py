from typer.testing import CliRunner

from skill_factory import __version__
from skill_factory.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output


def test_version_prints_sf_version(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import skill_factory.cli as cli

    # never start the real `claude` binary from a unit test
    monkeypatch.setattr(cli, "_installed_claude_version", lambda: "9.9.9")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"sf {__version__}" in result.output


def test_spec_validate_build_release_flow(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["spec", "validate"]).exit_code == 0
    stale = runner.invoke(app, ["build", "--check"])
    assert stale.exit_code == 1
    assert runner.invoke(app, ["build"]).exit_code == 0
    assert runner.invoke(app, ["build", "--check"]).exit_code == 0
    rel = runner.invoke(app, ["release", "alpha", "--bump", "major"])
    assert rel.exit_code == 0, rel.output
    assert "alpha: 1.0.0 -> 2.0.0" in rel.output
    assert runner.invoke(app, ["build", "--check"]).exit_code == 0  # release rebuilt


def test_spec_validate_fails_on_bad_skill(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    (repo / "skills" / "alpha" / "SKILL.md").write_text("no frontmatter\n")
    result = runner.invoke(app, ["spec", "validate"])
    assert result.exit_code == 1


def test_lint_cli_exit_codes(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    ok = runner.invoke(app, ["lint", "--format", "json"])
    assert ok.exit_code == 0  # fixture skills only produce warnings
    assert runner.invoke(app, ["lint", "--strict"]).exit_code == 1
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text() + "\nlog to /tmp/shared.log\n")
    bad = runner.invoke(app, ["lint", "alpha"])
    assert bad.exit_code == 1
    assert "SF010" in bad.output
    assert runner.invoke(app, ["lint", "nope"]).exit_code == 2


def test_eval_cli(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.eval import runner as eval_runner
    from skill_factory.eval.result import parse
    from skill_factory.models.spec import EvalPolicy

    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["eval"]).exit_code == 2  # nothing selected

    def fake_run_eval(skill, runs_dir, opts):  # type: ignore[no-untyped-def]
        raw = {
            "schemaVersion": 1,
            "costUsd": 0.5,
            "cases": [
                {
                    "name": "c",
                    "arms": {
                        "with": [{"passed": True, "score": 1}] * 4
                        + [{"passed": False, "score": 0}],
                        "without": [{"passed": False, "score": 0}] * 5,
                    },
                }
            ],
        }
        out = runs_dir / skill.name / "t"
        out.mkdir(parents=True)
        return parse(
            raw, skill=skill.name, version="1.0.0", model=opts.model, policy=EvalPolicy()
        ), out

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)
    ok = runner.invoke(app, ["eval", "alpha", "--runs", "5"])
    assert ok.exit_code == 0, ok.output
    assert "c: 4/5 (80%)" in ok.output and "baseline 0%" in ok.output


def test_report_and_statusline_cli(repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    (repo / "hooks").mkdir()
    shutil.copy(root / "hooks" / "sf_statusline.py", repo / "hooks")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("SF_HOME", str(tmp_path / "home"))
    rep = runner.invoke(app, ["report", "--out", "out/r.html"])
    assert rep.exit_code == 0, rep.output
    assert (repo / "out" / "r.html").read_text().startswith("<!doctype html>")
    settings = tmp_path / "settings.json"
    inst = runner.invoke(
        app, ["statusline", "install", "--settings", str(settings), "--yes", "--budget", "2"]
    )
    assert inst.exit_code == 0, inst.output
    assert "statusLine" in settings.read_text()
    assert (tmp_path / "home" / "config.json").read_text().count('"session_budget_usd": 2') == 1
    declined = runner.invoke(
        app, ["statusline", "install", "--settings", str(tmp_path / "other.json")], input="n\n"
    )
    assert declined.exit_code == 1 and not (tmp_path / "other.json").exists()


def test_new_and_compile_cli(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.compile import backend as backend_mod
    from tests.test_compile import FakeBackend, answer

    monkeypatch.chdir(repo)
    made = runner.invoke(app, ["new", "fresh", "--tier", "core", "--goal", "Be fresh."])
    assert made.exit_code == 0, made.output
    assert runner.invoke(app, ["new", "fresh"]).exit_code == 2  # exists
    fake = FakeBackend([answer(desc="Does fresh. Use when asked.", body=answer()["body"])])
    monkeypatch.setattr(backend_mod, "get_backend", lambda _name: fake)
    out = runner.invoke(app, ["compile", "fresh"])
    assert out.exit_code == 0, out.output
    assert "SKILL.md.new" in out.output and "+description: Does fresh" in out.output
    assert runner.invoke(app, ["compile", "--check"]).exit_code == 0
    assert runner.invoke(app, ["compile"]).exit_code == 2


def test_ablate_bench_judge_cli(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.compile import backend as backend_mod
    from skill_factory.eval import runner as eval_runner
    from tests.test_ablation import CAL, Judge, summary

    monkeypatch.chdir(repo)
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text() + "\n## Extra\n\nmore\n")
    calls = []

    def fake_run_eval(skill, runs_dir, opts, *, skill_md=None, label=None):  # type: ignore[no-untyped-def]
        calls.append((label, opts.model, opts.ablation))
        out = runs_dir / skill.name / (label or "plain")
        out.mkdir(parents=True, exist_ok=True)
        rate = [True] * 8 + [False] * 2 if skill_md is None else [True] * 4 + [False] * 6
        return summary(rate, 0.01 if opts.model == "cheap" else 0.05), out

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)
    ab = runner.invoke(app, ["ablate", "alpha"])
    assert ab.exit_code == 0, ab.output
    assert ("ablation-minus-01-extra", None, False) in calls and "keep section" in ab.output
    assert list((repo / "runs" / "alpha").glob("*-ablation.json"))
    assert runner.invoke(app, ["ablate", "alpha", "--section", "Nope"]).exit_code == 2

    b = runner.invoke(app, ["bench", "alpha", "--models", "cheap,pricey", "--write"])
    assert b.exit_code == 0, b.output
    assert "recommended default: cheap" in b.output
    from skill_factory.spec import load_skill

    assert load_skill(repo / "skills" / "alpha").spec.targets.models[0] == "cheap"

    cal = repo / "skills" / "alpha" / "evals" / "_calibration"
    cal.mkdir(parents=True)
    (cal / "seed.yaml").write_text(CAL)
    monkeypatch.setattr(
        backend_mod, "get_backend", lambda _n: Judge({"r1": ["PASS"] * 3, "r2": ["FAIL"] * 3})
    )
    ok = runner.invoke(app, ["judge", "calibrate", "alpha"])
    assert ok.exit_code == 0, ok.output
    assert "agreement 100%, Cohen's kappa 1.00" in ok.output
    monkeypatch.setattr(
        backend_mod, "get_backend", lambda _n: Judge({"r1": ["FAIL"] * 3, "r2": ["PASS"] * 3})
    )
    assert runner.invoke(app, ["judge", "calibrate", "alpha"]).exit_code == 1


def test_papercuts_search_analyze_cli(repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    from tests.test_phase8 import CUT_A, inbox

    home = tmp_path / "home"
    monkeypatch.setenv("SF_HOME", str(home))
    monkeypatch.chdir(repo)
    assert "no open papercuts" in runner.invoke(app, ["papercuts", "list"]).output
    inbox(home, CUT_A)
    assert "aaaa1111" in runner.invoke(app, ["papercuts", "list"]).output
    assert "alpha" in runner.invoke(app, ["papercuts", "group"]).output
    assert "a JSON score" in runner.invoke(app, ["papercuts", "show", "aaaa"]).output
    pro = runner.invoke(app, ["papercuts", "promote", "aaaa"])
    assert pro.exit_code == 0, pro.output
    assert "no open papercuts" in runner.invoke(app, ["papercuts", "list"]).output
    assert "promoted" in runner.invoke(app, ["papercuts", "list", "--status", "all"]).output
    assert runner.invoke(app, ["papercuts", "close", "aaaa", "--note", "fixed"]).exit_code == 0
    assert runner.invoke(app, ["papercuts", "show", "zzzz"]).exit_code == 2

    out = runner.invoke(app, ["search", "do alpha", "--json"])
    assert out.exit_code == 0 and json.loads(out.output)[0]["skill"] == "alpha"
    monkeypatch.setenv("SF_REPO", str(repo))
    monkeypatch.chdir(tmp_path)  # outside the repo: SF_REPO is used
    assert "alpha" in runner.invoke(app, ["search", "do alpha"]).output

    assert "no sessions" in runner.invoke(app, ["analyze", "--otel-logs", "/nonexistent"]).output


def test_release_reports_rebuild_failure(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    import skill_factory.cli as cli
    from skill_factory.market import BuildError

    def broken(_root):  # type: ignore[no-untyped-def]
        raise BuildError(["boom"])

    monkeypatch.setattr(cli, "build", broken)
    out = runner.invoke(app, ["release", "alpha"])
    assert out.exit_code == 1
    assert "versions were bumped" in out.output and "boom" in out.output


def test_lint_changed_edge_cases(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json
    import subprocess

    monkeypatch.chdir(repo)
    bad = runner.invoke(app, ["lint", "--changed"])  # not a git repo
    assert bad.exit_code == 2 and "git failed" in bad.output
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "i"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    out = runner.invoke(app, ["lint", "--changed", "--format", "json"])
    assert out.exit_code == 0 and json.loads(out.output)["skills"] == []
    assert runner.invoke(app, ["lint", "alpha", "--changed"]).exit_code == 2


def test_eval_changed_summary_install_agent_cli(repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    from skill_factory.eval import runner as eval_runner
    from tests.test_ablation import summary

    monkeypatch.chdir(repo)
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "i"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    md_out = tmp_path / "summary.md"
    none = runner.invoke(app, ["eval", "--changed", "--summary-md", str(md_out)])
    assert none.exit_code == 0 and "No changed skills" in md_out.read_text()

    (repo / "skills" / "alpha" / "evals" / "c").mkdir(parents=True)
    (repo / "skills" / "alpha" / "evals" / "c" / "case.yaml").write_text("name: c\n")
    monkeypatch.setattr(
        eval_runner, "run_eval", lambda s, r, o, **k: (summary([True] * 4 + [False], 0.1), r)
    )
    ran = runner.invoke(app, ["eval", "--changed", "--summary-md", str(md_out)])
    assert ran.exit_code == 0, ran.output
    assert "| `alpha` | ok | 80% |" in md_out.read_text()

    assert runner.invoke(app, ["build"]).exit_code == 0
    dest = tmp_path / "codex"
    inst = runner.invoke(app, ["install", "--agent", "codex", "--dest", str(dest), "--all-skills"])
    assert inst.exit_code == 0, inst.output
    assert (dest / "alpha" / "SKILL.md").is_file()
    assert runner.invoke(app, ["agent", "run", "nope"]).exit_code == 2


def test_untrusted_text_is_not_rich_markup(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    (repo / "skills" / "alpha" / "spec.yaml").write_text("name: [/oops]\n")
    out = runner.invoke(app, ["spec", "validate"])
    assert out.exit_code == 1 and "MarkupError" not in repr(out.exception)


def test_eval_rejects_bad_jobs_and_runs(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["eval", "alpha", "-j", "16"]).exit_code == 2
    assert runner.invoke(app, ["eval", "alpha", "--runs", "0"]).exit_code == 2


def test_compile_apply_with_lint_errors_keeps_skill_md(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.compile import backend as backend_mod
    from tests.test_compile import FakeBackend, answer

    monkeypatch.chdir(repo)
    before = (repo / "skills" / "alpha" / "SKILL.md").read_text()
    bad = answer(body=answer()["body"] + "\nlog to /tmp/shared.log\n")  # SF010 error
    monkeypatch.setattr(backend_mod, "get_backend", lambda _n: FakeBackend([bad, bad, bad]))
    out = runner.invoke(app, ["compile", "alpha", "--apply"])
    assert out.exit_code == 1
    assert (repo / "skills" / "alpha" / "SKILL.md").read_text() == before
    assert (repo / "skills" / "alpha" / "SKILL.md.new").is_file()
    assert runner.invoke(app, ["compile", "alpha", "--retries", "-1"]).exit_code == 2


def test_bench_refuses_unsafe_recommendations(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.eval import runner as eval_runner
    from skill_factory.eval.runner import EvalError
    from tests.test_ablation import summary

    monkeypatch.chdir(repo)

    def fake(skill, runs_dir, opts, **kw):  # type: ignore[no-untyped-def]
        if opts.model == "broken":
            raise EvalError("timeout")
        return summary([True, False, False], 0.01), runs_dir

    monkeypatch.setattr(eval_runner, "run_eval", fake)
    spec = (repo / "skills" / "alpha" / "spec.yaml").read_text()
    out = runner.invoke(app, ["bench", "alpha", "--models", "a,broken", "--write"])
    assert out.exit_code == 1 and "--write skipped" in out.output
    assert "no model meets min_pass_rate" in out.output and "no result for broken" in out.output
    assert (repo / "skills" / "alpha" / "spec.yaml").read_text() == spec


def test_judge_calibrate_vote_and_single_class_checks(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.compile import backend as backend_mod
    from tests.test_ablation import Judge

    monkeypatch.chdir(repo)
    cal = repo / "skills" / "alpha" / "evals" / "_calibration"
    cal.mkdir(parents=True)
    (cal / "s.yaml").write_text("- {id: a, criteria: c, response: r1, label: pass}\n")
    assert runner.invoke(app, ["judge", "calibrate", "alpha", "--votes", "2"]).exit_code == 2
    monkeypatch.setattr(backend_mod, "get_backend", lambda _n: Judge({"r1": ["PASS"] * 3}))
    out = runner.invoke(app, ["judge", "calibrate", "alpha"])
    assert out.exit_code == 3 and "same class" in out.output


def test_search_reports_a_bad_repo(repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    (repo / "market.yaml").unlink()
    out = runner.invoke(app, ["search", "alpha"])
    assert out.exit_code == 2 and "not a skill-factory repo" in out.output
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.json").write_text("{bad")
    monkeypatch.setenv("SF_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["search", "alpha"]).exit_code == 2


def test_eval_summary_lists_errored_skills_and_cli_messages(repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from skill_factory.eval import runner as eval_runner
    from skill_factory.eval.runner import EvalError

    monkeypatch.chdir(repo)
    (repo / "skills" / "alpha" / "evals" / "c").mkdir(parents=True)
    (repo / "skills" / "alpha" / "evals" / "c" / "case.yaml").write_text("name: c\n")

    def broken(*a, **k):  # type: ignore[no-untyped-def]
        raise EvalError("broken case.yaml")

    monkeypatch.setattr(eval_runner, "run_eval", broken)
    md = tmp_path / "s.md"
    out = runner.invoke(app, ["eval", "alpha", "--summary-md", str(md)])
    assert out.exit_code == 1 and "| `alpha` | error |" in md.read_text()
    assert "name an agent or pass --all" in runner.invoke(app, ["agent", "run"]).output
    bad = runner.invoke(app, ["install", "--agent", "codex", "nope"])
    assert bad.exit_code == 2 and "Traceback" not in bad.output
