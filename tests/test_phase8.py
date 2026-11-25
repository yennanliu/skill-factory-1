from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from skill_factory import papercuts as pc
from skill_factory.analyze import analyze
from skill_factory.market import build
from skill_factory.market.build import catalog, load_all, load_market
from skill_factory.search import search, tokens
from skill_factory.spec import load_skill
from skill_factory.telemetry.ingest import DDL, OTEL_COLS

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "skills" / "log-papercut" / "scripts" / "log_papercut.py"
FIND = ROOT / "skills" / "find-skills" / "scripts" / "find_skills.py"
T0 = datetime(2026, 11, 24, 9)


def run(script: Path, *args: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"} | ({"SF_HOME": str(home)} if home else {})
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, env=env, timeout=20
    )


# --- log-papercut script ---------------------------------------------------------------


def test_log_papercut_appends_a_report(tmp_path: Path) -> None:
    out = run(
        LOG,
        "--skill",
        "sf-core:demo",
        "--tried",
        "ran step 2",
        "--expected",
        "ok",
        "--happened",
        "exit 127",
        "--severity",
        "med",
        "--suggested-fix",
        "rename",
        home=tmp_path,
    )
    assert out.returncode == 0 and "filed for sf-core:demo" in out.stdout
    (line,) = (tmp_path / "papercuts" / "inbox.jsonl").read_text().splitlines()
    rec = json.loads(line)
    assert (rec["skill"], rec["severity"], rec["suggested_fix"]) == (
        "sf-core:demo",
        "med",
        "rename",
    )
    assert len(rec["id"]) == 8 and rec["cwd_hash"].startswith("sha256:")


def test_log_papercut_truncates_and_survives_unwritable_home(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    out = run(
        LOG,
        "--skill",
        "s",
        "--tried",
        "x" * 2000,
        "--expected",
        "e",
        "--happened",
        "h",
        home=blocker,
    )
    assert out.returncode == 0 and "could not write the papercut inbox" in out.stderr
    rec = json.loads(out.stderr.split("report: ", 1)[1])
    assert len(rec["tried"]) == 500


def test_log_papercut_rejects_bad_severity(tmp_path: Path) -> None:
    out = run(
        LOG,
        "--skill",
        "s",
        "--tried",
        "t",
        "--expected",
        "e",
        "--happened",
        "h",
        "--severity",
        "medium",
        home=tmp_path,
    )
    assert out.returncode == 2 and "invalid choice" in out.stderr


# --- papercut triage -------------------------------------------------------------------


def inbox(home: Path, *cuts: dict[str, Any]) -> None:
    p = pc.inbox_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(c) + "\n" for c in cuts) + "garbage\n")


CUT_A = {
    "id": "aaaa1111",
    "ts": "2026-11-24T10:00:00Z",
    "skill": "sf-core:alpha",
    "tried": "t",
    "expected": "a JSON score",
    "happened": "exit 127",
    "suggested_fix": "rename",
    "severity": "high",
}
CUT_B = {**CUT_A, "id": "bbbb2222", "severity": "low", "skill": "beta"}
CUT_C = {**CUT_A, "id": "aaab3333", "severity": "med"}


def test_triage_load_group_find(tmp_path: Path) -> None:
    inbox(tmp_path, CUT_A, CUT_B, CUT_C)
    cuts = pc.load(tmp_path)
    assert [c.id for c in cuts] == ["aaaa1111", "bbbb2222", "aaab3333"]
    assert [(s, n, dict(sev)) for s, n, sev in pc.group(cuts)] == [
        ("alpha", 2, {"high": 1, "med": 1}),
        ("beta", 1, {"low": 1}),
    ]
    assert pc.find(cuts, "bbbb").id == "bbbb2222"  # type: ignore[union-attr]
    assert pc.find(cuts, "aaa") is None  # ambiguous prefix


def test_promote_writes_a_draft_case(repo: Path, tmp_path: Path) -> None:
    inbox(tmp_path, CUT_A)
    (cut,) = pc.load(tmp_path)
    case = pc.promote(repo, cut)
    assert case == repo / "skills" / "alpha" / "evals" / "papercut-aaaa1111" / "case.yaml"
    text = case.read_text()
    assert "papercut aaaa1111" in text and "a JSON score" in text and "- papercut" in text
    with pytest.raises(FileExistsError):
        pc.promote(repo, cut)
    with pytest.raises(FileNotFoundError):
        pc.promote(repo, pc.Papercut.from_json({**CUT_A, "skill": "nope"}))
    pc.mark(tmp_path, cut.id, "promoted", "x")
    assert pc.status_of(cut.id, pc.load_state(tmp_path)) == "promoted"
    assert pc.status_of("other", pc.load_state(tmp_path)) == "open"


# --- search + catalog ------------------------------------------------------------------


def test_tokens() -> None:
    assert {"test", "tests", "flaky"} <= tokens("find flaky tests for me")
    assert "the" not in tokens("the and for")


def test_search_ranks_repo_skills(tmp_path: Path) -> None:
    skills = load_all(ROOT)
    hits = search("review my python code for bugs", skills, tmp_path, "skill-factory")
    assert hits[0].skill == "python-code-reviewer"
    assert hits[0].install == "/plugin install sf-core@skill-factory"
    assert search("zzz qqq", skills, tmp_path, "m") == []


def test_catalog_is_deterministic_and_bundled(repo: Path) -> None:
    spec = repo / "skills" / "alpha" / "spec.yaml"
    spec.write_text(spec.read_text() + "catalog: true\n")
    build(repo)
    path = repo / "plugins" / "sf-core" / "skills" / "alpha" / "references" / "catalog.json"
    data = json.loads(path.read_text())
    assert [s["skill"] for s in data["skills"]] == ["alpha", "beta"]
    assert data["skills"][0]["install"] == "/plugin install sf-core@test-market"
    assert data == catalog(load_all(repo), load_market(repo))
    assert not (
        repo / "plugins" / "sf-domain-demo" / "skills" / "beta" / "references" / "catalog.json"
    ).exists()


def test_find_skills_script_against_built_catalog() -> None:
    script = (
        ROOT / "plugins" / "sf-platform" / "skills" / "find-skills" / "scripts" / "find_skills.py"
    )
    out = run(script, "file my leetcode solution into the repo", "--limit", "2")
    assert out.returncode == 0, out.stderr
    hits = json.loads(out.stdout)
    assert hits[0]["skill"] == "lc-add" and hits[0]["install"].startswith("/plugin install ")
    assert json.loads(run(script, "zzz").stdout) == []
    # the source copy has no catalog (it is generated at build time)
    assert run(FIND, "anything").returncode == 2


def test_stage_bundles_catalog(tmp_path: Path) -> None:
    from skill_factory.eval.stage import stage_skill

    stage = stage_skill(load_skill(ROOT / "skills" / "find-skills"), tmp_path / "s")
    assert (stage / "skills" / "find-skills" / "references" / "catalog.json").is_file()


# --- session analyzer ------------------------------------------------------------------


def con_with(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(DDL)
    con.executemany(
        f"INSERT INTO otel_events VALUES ({', '.join('?' * len(OTEL_COLS))})",
        [tuple(r.get(c) for c in OTEL_COLS) for r in rows],
    )
    return con


def req(sid: str, minute: float, **kw: Any) -> dict[str, Any]:
    return {
        "ts": T0 + timedelta(minutes=minute),
        "event": "api_request",
        "session_id": sid,
        "prompt_id": kw.pop("pid", "p1"),
        "model": "claude-sonnet-5-5",
        "cost_usd": 0.01,
        "output_tokens": 500,
        "input_tokens": 100,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        **kw,
    }


def tool(sid: str, minute: float, name: str, **kw: Any) -> dict[str, Any]:
    return {
        "ts": T0 + timedelta(minutes=minute),
        "event": "tool_result",
        "session_id": sid,
        "prompt_id": kw.pop("pid", "p1"),
        "tool_name": name,
        "success": kw.pop("ok", True),
        **kw,
    }


def patterns(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {s.id: [f.pattern for f in s.findings] for s in analyze(con_with(rows), T0)}


def test_clean_session_has_no_findings() -> None:
    assert patterns([req("s", 0), tool("s", 1, "Read"), tool("s", 2, "Edit"), req("s", 3)]) == {
        "s": []
    }


def test_each_detector() -> None:
    rows = [
        req("pre", 0, cache_creation_tokens=40_000),
        req("gap", 0),
        req("gap", 90, cache_creation_tokens=25_000),
        req("opus", 0, model="claude-opus-5-5", output_tokens=50),
        tool("opus", 1, "Read"),
        tool("opus", 2, "Grep"),
        req("big", 0),
        tool("big", 1, "Bash", result_bytes=45_000),
        req("poll", 0),
        *[tool("poll", i, "Bash", tool_input='{"command":"gh pr checks 7"}') for i in range(1, 5)],
        req("retry", 0),
        *[tool("retry", i, "Bash", ok=False, tool_input='{"command":"make"}') for i in range(1, 4)],
        req("thrash", 0),
        *[tool("thrash", i, "Grep") for i in range(1, 11)],
        tool("thrash", 12, "Edit"),
    ]
    found = patterns(rows)
    assert found["pre"] == ["heavy-preloaded-context"]
    assert found["gap"] == ["cache-expired-after-break"]
    assert found["opus"] == ["expensive-model-easy-work"]
    assert found["big"] == ["large-tool-result"]
    assert found["poll"] == ["polling-in-main-session"]
    assert found["retry"] == ["retry-loop"]
    assert found["thrash"] == ["search-thrash"]


def test_sessions_sorted_by_cost() -> None:
    sessions = analyze(con_with([req("cheap", 0), req("dear", 0, cost_usd=2.0)]), T0)
    assert [s.id for s in sessions] == ["dear", "cheap"]
    assert sessions[0].cost == pytest.approx(2.0) and sessions[0].start == T0


def test_stage_catalog_survives_a_broken_neighbour(tmp_path: Path) -> None:
    import shutil

    from skill_factory.eval.stage import stage_skill

    repo = tmp_path / "r"
    shutil.copytree(ROOT / "skills", repo / "skills")
    shutil.copy(ROOT / "market.yaml", repo / "market.yaml")
    (repo / "skills" / "half-written").mkdir()
    (repo / "skills" / "half-written" / "spec.yaml").write_text("name: [broken\n")
    stage = stage_skill(load_skill(repo / "skills" / "find-skills"), tmp_path / "s")
    data = json.loads(
        (stage / "skills" / "find-skills" / "references" / "catalog.json").read_text()
    )
    assert "half-written" not in {s["skill"] for s in data["skills"]}


def test_promote_escapes_multiline_text_and_rejects_unsafe_ids(repo: Path, tmp_path: Path) -> None:
    from ruamel.yaml import YAML

    cut = pc.Papercut.from_json({**CUT_A, "suggested_fix": "use make all\nexecution: {prompt: x}"})
    case = pc.promote(repo, cut)
    doc = YAML(typ="safe").load(case.read_text())
    assert doc["execution"]["prompt"].startswith("TODO")  # the injected line stayed a comment
    with pytest.raises(FileNotFoundError, match="unsafe"):
        pc.promote(repo, pc.Papercut.from_json({**CUT_A, "id": "../../x"}))


def test_find_tolerates_duplicate_lines_of_one_report() -> None:
    cuts = [pc.Papercut.from_json(CUT_A), pc.Papercut.from_json(CUT_A)]
    found = pc.find(cuts, "aaaa1111")
    assert found is not None and found.id == "aaaa1111"


def test_analyze_uses_the_sessions_real_first_request() -> None:
    rows = [
        req("old", -60 * 24 * 8, input_tokens=100),  # started 8 days before T0
        req("old", 0, cache_read_tokens=120_000),
    ]  # big context inside the window
    found = {s.id: s for s in analyze(con_with(rows), T0)}
    assert found["old"].findings == []
    assert found["old"].start == T0 - timedelta(days=8)


def test_streaks_count_only_matching_outcomes() -> None:
    cmd = '{"command":"pytest"}'
    rows = [
        req("s", 0),
        tool("s", 1, "Bash", tool_input=cmd),
        tool("s", 2, "Bash", tool_input=cmd, ok=False),
        tool("s", 3, "Bash", tool_input=cmd, ok=False),
    ]
    assert patterns(rows) == {"s": []}  # 1 success + 2 failures is not "failed 3x"
    long_a = '{"command":"python - <<EOF ' + "x" * 100 + 'A"}'
    long_b = long_a.replace('A"}', 'B"}')
    rows = [
        req("t", 0),
        *[tool("t", i, "Bash", tool_input=v) for i, v in enumerate([long_a, long_b, long_a], 1)],
    ]
    assert patterns(rows) == {"t": []}  # same 80-char prefix, different commands
