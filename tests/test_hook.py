"""The telemetry hook, replayed against real Claude Code payloads captured by the Phase 0 probe."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "sf_telemetry.py"
PINNED = (ROOT / ".claude-version").read_text().strip()
FIXTURES = ROOT / "tests" / "fixtures" / "claude" / PINNED


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sf_telemetry", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def replay(case: str, base: Path) -> list[dict[str, Any]]:
    hook = load_hook()
    events = []
    for f in sorted((FIXTURES / case / "hooks").glob("*.json")):
        e = hook.build_event(json.loads(f.read_text()), base)
        if e is not None:
            events.append({k: v for k, v in e.items() if k not in ("ts", "session_id", "cwd_hash")})
    return events


def test_model_triggered_session(tmp_path: Path, snapshot: Any) -> None:
    events = replay("model_triggered", tmp_path)
    assert [e["event"] for e in events] == [
        "session_start",
        "skill_invoked",
        "tool_error",
        "session_end",
    ]
    invoked, error = events[1], events[2]
    assert invoked["trigger"] == "model"
    assert (invoked["plugin"], invoked["skill"]) == ("sf-probe", "probe-echo")
    assert (error["tool"], error["error_code"], error["qualified"]) == (
        "Bash",
        1,
        "sf-probe:probe-echo",
    )
    assert events == snapshot


def test_user_triggered_session(tmp_path: Path, snapshot: Any) -> None:
    events = replay("user_triggered", tmp_path)
    assert [e["event"] for e in events] == [
        "session_start",
        "skill_invoked",
        "tool_error",
        "session_end",
    ]
    assert events[1]["trigger"] == "user"
    assert events[1]["source"] == "plugin"
    assert events[2]["skill"] == "probe-echo"
    assert events == snapshot


def test_session_state_is_cleaned_up(tmp_path: Path) -> None:
    replay("model_triggered", tmp_path)
    assert list((tmp_path / "sessions").glob("*.json")) == []


def test_events_never_contain_prompt_or_paths(tmp_path: Path) -> None:
    for case in ("model_triggered", "user_triggered"):
        hook = load_hook()
        for f in sorted((FIXTURES / case / "hooks").glob("*.json")):
            e = hook.build_event(json.loads(f.read_text()), tmp_path)
            text = json.dumps(e)
            assert "<TMP>" not in text and "<HOME>" not in text  # raw cwd/transcript paths
            assert "nonexistent-sf-probe" not in text  # tool input / error text


def run_hook(payload: str, sf_home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        text=True,
        capture_output=True,
        env={"SF_HOME": str(sf_home), "PATH": "/usr/bin:/bin"},
        timeout=10,
    )


def test_hook_process_appends_one_line(tmp_path: Path) -> None:
    payload = (FIXTURES / "model_triggered" / "hooks" / "03-PostToolUse.json").read_text()
    start = time.perf_counter()
    out = run_hook(payload, tmp_path)
    elapsed = time.perf_counter() - start
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""
    lines = (tmp_path / "telemetry" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "skill_invoked"
    assert elapsed < 1.0  # interpreter start dominates; the hook itself is ~ms


@pytest.mark.parametrize("payload", ["", "not json", "[]", '{"hook_event_name": "Stop"}'])
def test_hook_never_fails(tmp_path: Path, payload: str) -> None:
    out = run_hook(payload, tmp_path)
    assert out.returncode == 0
    assert not (tmp_path / "telemetry" / "events.jsonl").exists()


def test_hooks_json_wires_the_contract_events() -> None:
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(cfg) == {
        "SessionStart",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
        "SessionEnd",
    }
    assert cfg["PostToolUse"][0]["matcher"] == "Skill"
    for entries in cfg.values():
        cmd = entries[0]["hooks"][0]["command"]
        assert cmd == 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/sf_telemetry.py"'


def _p(event: str, **kw: Any) -> dict[str, Any]:
    return {"hook_event_name": event, "session_id": "s1", "cwd": "/w", **kw}


def test_stop_ends_skill_attribution(tmp_path: Path) -> None:
    hook = load_hook()
    hook.build_event(_p("PostToolUse", tool_name="Skill", tool_input={"skill": "p:a"}), tmp_path)
    err = hook.build_event(
        _p("PostToolUseFailure", tool_name="Bash", error="Exit code 1"), tmp_path
    )
    assert err["qualified"] == "p:a"
    assert hook.build_event(_p("Stop"), tmp_path) is None
    later = hook.build_event(
        _p("PostToolUseFailure", tool_name="Bash", error="Exit code 2"), tmp_path
    )
    assert later["qualified"] is None and later["error_code"] == 2


def test_compaction_keeps_the_active_skill(tmp_path: Path) -> None:
    hook = load_hook()
    hook.build_event(_p("SessionStart", source="startup", model="m1"), tmp_path)
    hook.build_event(_p("PostToolUse", tool_name="Skill", tool_input={"skill": "p:a"}), tmp_path)
    hook.build_event(_p("SessionStart", source="compact"), tmp_path)
    err = hook.build_event(_p("PostToolUseFailure", tool_name="Bash", error="x"), tmp_path)
    assert err["qualified"] == "p:a" and err["model"] == "m1"
    hook.build_event(_p("SessionStart", source="clear"), tmp_path)
    err = hook.build_event(_p("PostToolUseFailure", tool_name="Bash", error="x"), tmp_path)
    assert err["qualified"] is None


def test_plain_custom_command_is_not_a_skill(tmp_path: Path) -> None:
    hook = load_hook()
    e = hook.build_event(
        _p(
            "UserPromptExpansion",
            expansion_type="slash_command",
            command_name="deploy",
            command_source="projectSettings",
        ),
        tmp_path,
    )
    assert e["event"] == "command_invoked" and e["command"] == "deploy"
    err = hook.build_event(_p("PostToolUseFailure", tool_name="Bash", error="x"), tmp_path)
    assert err["qualified"] is None
