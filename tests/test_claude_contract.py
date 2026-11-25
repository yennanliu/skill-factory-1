"""Assert the Claude Code facts in docs/CLAUDE_CODE_CONTRACT.md against captured probe fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINNED = (ROOT / ".claude-version").read_text().strip()
FIXTURES = ROOT / "tests" / "fixtures" / "claude" / PINNED


def hooks(case: str) -> list[dict[str, Any]]:
    files = sorted((FIXTURES / case / "hooks").glob("*.json"))
    return [json.loads(f.read_text()) for f in files]


def stream(case: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / case / "stream.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def by_event(case: str, event: str) -> list[dict[str, Any]]:
    return [h for h in hooks(case) if h["hook_event_name"] == event]


def test_fixtures_exist_for_pinned_version() -> None:
    assert (FIXTURES / "model_triggered").is_dir(), f"run scripts/probe_claude.sh for {PINNED}"
    assert (FIXTURES / "user_triggered").is_dir()


def test_model_triggered_skill_is_a_skill_tool_call() -> None:
    post = [h for h in by_event("model_triggered", "PostToolUse") if h["tool_name"] == "Skill"]
    assert len(post) == 1
    assert post[0]["tool_input"] == {"skill": "sf-probe:probe-echo"}
    assert post[0]["tool_response"]["commandName"] == "sf-probe:probe-echo"
    assert by_event("model_triggered", "UserPromptExpansion") == []


def test_user_triggered_skill_is_a_prompt_expansion_not_a_tool_call() -> None:
    (exp,) = by_event("user_triggered", "UserPromptExpansion")
    assert exp["command_name"] == "sf-probe:probe-echo"
    assert exp["command_source"] == "plugin"
    assert exp["expansion_type"] == "slash_command"
    tools = {h["tool_name"] for h in by_event("user_triggered", "PreToolUse")}
    assert "Skill" not in tools


@pytest.mark.parametrize("case", ["model_triggered", "user_triggered"])
def test_common_hook_fields(case: str) -> None:
    for h in hooks(case):
        for key in ("session_id", "transcript_path", "cwd", "hook_event_name"):
            assert key in h, (h["hook_event_name"], key)


@pytest.mark.parametrize("case", ["model_triggered", "user_triggered"])
def test_failing_tool_fires_post_tool_use_failure(case: str) -> None:
    (fail,) = by_event(case, "PostToolUseFailure")
    assert fail["tool_name"] == "Bash"
    # GNU ls exits 2, BSD ls exits 1: assert a non-zero code, not a platform's value
    code = fail["error"].split("\n", 1)[0].removeprefix("Exit code ")
    assert code.isdigit() and int(code) != 0, fail["error"]
    failed_ids = {fail["tool_use_id"]}
    ok_ids = {h["tool_use_id"] for h in by_event(case, "PostToolUse")}
    assert failed_ids.isdisjoint(ok_ids)


@pytest.mark.parametrize("case", ["model_triggered", "user_triggered"])
def test_stream_result_fields(case: str) -> None:
    events = stream(case)
    init = next(e for e in events if e["type"] == "system" and e.get("subtype") == "init")
    assert init["claude_code_version"] == PINNED
    assert "sf-probe:probe-echo" in init["skills"]
    result = events[-1]
    assert result["type"] == "result"
    for key in ("subtype", "is_error", "num_turns", "total_cost_usd", "usage", "modelUsage"):
        assert key in result, key
    assert result["usage"]["output_tokens"] > 0


def test_stream_shows_skill_tool_use_only_when_model_triggered() -> None:
    def tool_names(case: str) -> list[str]:
        return [
            c["name"]
            for e in stream(case)
            if e["type"] == "assistant"
            for c in e["message"]["content"]
            if c.get("type") == "tool_use"
        ]

    assert tool_names("model_triggered")[0] == "Skill"
    assert "Skill" not in tool_names("user_triggered")


def test_permission_block_fires_no_post_tool_hook() -> None:
    events = {h["hook_event_name"] for h in hooks("permission_blocked")}
    assert "PreToolUse" in events
    assert "PostToolUse" not in events and "PostToolUseFailure" not in events
    denied = [
        e
        for e in stream("permission_blocked")
        if e["type"] == "system" and e.get("subtype") == "permission_denied"
    ]
    assert denied and denied[0]["tool_name"] == "Bash"


def test_tool_ids_are_consistent_after_sanitizing() -> None:
    raw = (FIXTURES / "model_triggered" / "stream.jsonl").read_text()
    assert "toolu_" not in raw  # ids in values AND dict keys go through the same mapping
