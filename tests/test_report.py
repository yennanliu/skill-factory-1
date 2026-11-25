from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from skill_factory.report.data import build_rows, latest_eval, usage_by_skill
from skill_factory.report.render import render, sparkline
from skill_factory.spec import discover, load_skill
from skill_factory.telemetry.ingest import connect

ROOT = Path(__file__).resolve().parents[1]
PINNED = (ROOT / ".claude-version").read_text().strip()
OTEL_LOGS = ROOT / "tests" / "fixtures" / "otel" / PINNED / "logs.jsonl"
EPOCH, NOW = datetime(2000, 1, 1), datetime(2026, 11, 24, 23)


def write_summary(runs: Path, skill: str, stamp: str, rate: float, base: float) -> None:
    d = runs / skill / stamp
    d.mkdir(parents=True)
    (d / "summary.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "model": "m",
                "pass_rate": rate,
                "interval_95": [0.5, 0.9],
                "baseline_pass_rate": base,
                "runs": 6,
                "total_cost_usd": 0.4,
                "cases": [
                    {
                        "name": "c",
                        "runs": 3,
                        "passes": 2,
                        "baseline_pass_rate": base,
                        "fired_runs": 1,
                    }
                ],
            }
        )
    )


def hook_events(path: Path) -> Path:
    rows = [
        ("2026-11-10T10:00:00Z", "s1", "p:a", "h1"),
        ("2026-11-20T10:00:00Z", "s2", "p:a", "h2"),
        ("2026-11-20T10:05:00Z", "s2", "p:b", "h2"),
        ("2026-11-24T09:00:00Z", "s3", "p:a", "h1"),
    ]
    path.write_text(
        "".join(
            json.dumps(
                {
                    "ts": ts,
                    "event": "skill_invoked",
                    "session_id": sid,
                    "qualified": q,
                    "skill": q[2:],
                    "plugin": "p",
                    "trigger": "model",
                    "cwd_hash": h,
                }
            )
            + "\n"
            for ts, sid, q, h in rows
        )
    )
    return path


def test_usage_from_otel_fixture() -> None:
    usage = usage_by_skill(connect(Path("/none"), OTEL_LOGS), EPOCH, NOW, 14)
    u = usage["sf-probe:probe-echo"]
    assert (u.calls, u.model_calls, u.user_calls) == (2, 1, 1)
    assert u.versions == ["0.0.1"]
    assert u.cost_mean is not None and u.cost_p90 is not None
    assert u.cost_p90 >= u.cost_mean > 0
    assert u.requests_mean is not None and u.requests_mean >= 1
    assert len(u.daily) == 14


def test_usage_from_hook_events(tmp_path: Path) -> None:
    usage = usage_by_skill(
        connect(hook_events(tmp_path / "e.jsonl"), Path("/none")), EPOCH, NOW, 14
    )
    a = usage["p:a"]
    assert (a.calls, a.sessions, a.repos) == (3, 3, 2)
    assert a.active_weeks == 3 and a.retained
    assert a.co_used == [("p:b", 1)]
    assert sum(a.daily) == 2  # the 2026-11-10 call is outside the 14-day sparkline
    assert a.daily[-1] == 1 and a.cost_mean is None


def test_latest_eval_picks_newest(tmp_path: Path) -> None:
    assert latest_eval(tmp_path, "x") is None
    write_summary(tmp_path, "x", "2026-11-01T00-00-00Z", 0.5, 0.5)
    write_summary(tmp_path, "x", "2026-11-02T00-00-00Z", 0.9, 0.4)
    e = latest_eval(tmp_path, "x")
    assert e is not None and e.when == "2026-11-02T00-00-00Z"
    assert e.verdict == "helps" and e.lift is not None and round(e.lift, 2) == 0.5


def test_verdicts(tmp_path: Path) -> None:
    for i, (rate, base, verdict) in enumerate(
        [(0.8, 0.8, "no measurable value"), (0.8, 0.7, "small lift"), (0.9, 0.5, "helps")]
    ):
        write_summary(tmp_path, f"s{i}", "2026-11-01T00-00-00Z", rate, base)
        e = latest_eval(tmp_path, f"s{i}")
        assert e is not None and e.verdict == verdict


def test_rows_and_render_for_repo_skills(tmp_path: Path) -> None:
    skills = [load_skill(p) for p in discover(ROOT)]
    write_summary(tmp_path, "lc-add", "2026-11-01T00-00-00Z", 0.9, 0.6)
    usage = usage_by_skill(connect(Path("/none"), OTEL_LOGS), EPOCH, NOW, 14)
    rows, others = build_rows(skills, usage, tmp_path)
    assert {r.name for r in rows} == {s.name for s in skills}
    assert list(others) == ["sf-probe:probe-echo"]
    lc_add = next(r for r in rows if r.name == "lc-add")
    assert lc_add.qualified == "sf-domain-leetcode:lc-add"
    assert lc_add.eval is not None and lc_add.always_on_tokens > 0 and lc_add.on_invoke_tokens > 0
    html = render(
        rows, others, since=EPOCH, generated=NOW, sources={"events": "e", "otel": "o", "runs": "r"}
    )
    assert html.startswith("<!doctype html>")
    assert "<title>Skill Factory Report</title>" in html
    assert 'id="lc-coach"' in html and "not evaluated" in html and "helps" in html
    assert "sf-probe:probe-echo" in html
    assert "prefers-color-scheme: dark" in html and "<script" not in html


def test_sparkline() -> None:
    assert "<polyline" not in sparkline([0, 0, 0])
    svg = sparkline([0, 2, 1])
    assert 'aria-label="calls per day, max 2"' in svg and "<polyline" in svg


def test_latest_eval_ignores_labelled_runs(tmp_path: Path) -> None:
    write_summary(tmp_path, "x", "2026-11-01T00-00-00Z", 0.8, 0.4)
    write_summary(tmp_path, "x", "2026-11-02T00-00-00Z-bench-claude-haiku-5-5", 0.1, 0.1)
    write_summary(tmp_path, "x", "2026-11-03T00-00-00Z-ablation-minus-steps", 0.2, 0.2)
    e = latest_eval(tmp_path, "x")
    assert e is not None and e.when == "2026-11-01T00-00-00Z"
