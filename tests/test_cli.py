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


def test_untrusted_text_is_not_rich_markup(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    (repo / "skills" / "alpha" / "spec.yaml").write_text("name: [/oops]\n")
    out = runner.invoke(app, ["spec", "validate"])
    assert out.exit_code == 1 and "MarkupError" not in repr(out.exception)


def test_eval_rejects_bad_jobs_and_runs(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["eval", "alpha", "-j", "16"]).exit_code == 2
    assert runner.invoke(app, ["eval", "alpha", "--runs", "0"]).exit_code == 2
