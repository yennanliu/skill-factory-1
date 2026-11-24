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
        "UserPromptExpansion",
        "PostToolUse",
        "PostToolUseFailure",
        "SessionEnd",
    }
    assert cfg["PostToolUse"][0]["matcher"] == "Skill"
    for entries in cfg.values():
        cmd = entries[0]["hooks"][0]["command"]
        assert cmd == 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/sf_telemetry.py"'
