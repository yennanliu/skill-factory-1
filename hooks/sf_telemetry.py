#!/usr/bin/env python3
"""skill-factory telemetry hook. Standard library only; must never slow down or break a session.

Reads one Claude Code hook payload on stdin and appends at most one event line to
$SF_HOME/telemetry/events.jsonl (default ~/.skill-factory). Mapping (CLAUDE_CODE_CONTRACT.md):

  PostToolUse (tool_name == "Skill")       -> skill_invoked, trigger=model
  UserPromptExpansion (slash command)      -> skill_invoked, trigger=user
  PostToolUseFailure                       -> tool_error (attributed to the session's last skill)
  SessionStart / SessionEnd                -> session_start / session_end

Privacy: never records prompt text, command args, tool input/output, error text or raw paths.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def telemetry_dir() -> Path:
    home = os.environ.get("SF_HOME") or str(Path.home() / ".skill-factory")
    return Path(home).expanduser() / "telemetry"


def _split(qualified: str) -> tuple[str | None, str]:
    plugin, _, skill = qualified.rpartition(":")
    return (plugin or None), skill


def _state_path(base: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80] or "unknown"
    return base / "sessions" / f"{safe}.json"


def _read_state(base: Path, session_id: str) -> dict[str, Any]:
    try:
        return dict(json.loads(_state_path(base, session_id).read_text()))
    except (OSError, ValueError):
        return {}


def _write_state(base: Path, session_id: str, state: dict[str, Any]) -> None:
    path = _state_path(base, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def build_event(payload: dict[str, Any], base: Path) -> dict[str, Any] | None:
    """Map a hook payload to an event dict (or None to record nothing). Updates session state."""
    hook = payload.get("hook_event_name")
    session_id = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "")
    state = _read_state(base, session_id) if session_id else {}
    event: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "cwd_hash": "sha256:" + hashlib.sha256(cwd.encode()).hexdigest()[:16] if cwd else None,
        "model": state.get("model"),
    }

    if hook == "SessionStart":
        if session_id:
            _write_state(base, session_id, {"model": payload.get("model")})
        return {
            **event,
            "event": "session_start",
            "source": payload.get("source"),
            "model": payload.get("model"),
        }

    if hook == "SessionEnd":
        if session_id:
            with contextlib.suppress(OSError):
                _state_path(base, session_id).unlink()
        return {**event, "event": "session_end"}

    qualified = None
    trigger = None
    if hook == "PostToolUse" and payload.get("tool_name") == "Skill":
        qualified = str((payload.get("tool_input") or {}).get("skill") or "")
        trigger = "model"
    elif hook == "UserPromptExpansion" and payload.get("expansion_type") == "slash_command":
        qualified = str(payload.get("command_name") or "")
        trigger = "user"
    if qualified:
        plugin, skill = _split(qualified)
        if session_id:
            _write_state(base, session_id, {**state, "skill": qualified})
        return {
            **event,
            "event": "skill_invoked",
            "skill": skill,
            "plugin": plugin,
            "qualified": qualified,
            "trigger": trigger,
            "source": payload.get("command_source"),
        }

    if hook == "PostToolUseFailure":
        last = state.get("skill")
        plugin, skill = _split(last) if last else (None, None)
        code = re.match(r"Exit code (\d+)", str(payload.get("error") or ""))
        return {
            **event,
            "event": "tool_error",
            "tool": payload.get("tool_name"),
            "error_code": int(code.group(1)) if code else None,
            "interrupted": bool(payload.get("is_interrupt")),
            "skill": skill,
            "plugin": plugin,
            "qualified": last,
        }

    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        base = telemetry_dir()
        event = build_event(payload, base)
        if event is not None:
            base.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, separators=(",", ":")) + "\n"
            fd = os.open(base / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode())  # one O_APPEND write per line: safe across sessions
            finally:
                os.close(fd)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
