"""Load hook events (events.jsonl) and OTel logs (logs.jsonl) into DuckDB tables.

Tables
  hook_events(ts, event, session_id, skill, plugin, qualified, trigger, model, tool, error_code,
              cwd_hash)
  otel_events(ts, event, session_id, prompt_id, qualified, trigger, model, cost_usd,
              input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
              tool_name, success, duration_ms, plugin_name, plugin_version)

`qualified` is the skill name as Claude Code reports it ("<plugin>:<skill>", or "<skill>").
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

# OTel skill_activated.invocation_trigger -> our trigger vocabulary (see CLAUDE_CODE_CONTRACT.md)
OTEL_TRIGGERS = {"claude-proactive": "model", "user-slash": "user"}

HOOK_COLS = [
    "ts",
    "event",
    "session_id",
    "skill",
    "plugin",
    "qualified",
    "trigger",
    "model",
    "tool",
    "error_code",
    "cwd_hash",
    "interrupted",
]
OTEL_COLS = [
    "ts",
    "event",
    "session_id",
    "prompt_id",
    "qualified",
    "trigger",
    "model",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "tool_name",
    "success",
    "duration_ms",
    "plugin_name",
    "plugin_version",
    "tool_input",
    "result_bytes",
]

DDL = """
CREATE OR REPLACE TABLE hook_events (
  ts TIMESTAMP, event VARCHAR, session_id VARCHAR, skill VARCHAR, plugin VARCHAR,
  qualified VARCHAR, trigger VARCHAR, model VARCHAR, tool VARCHAR, error_code INTEGER,
  cwd_hash VARCHAR, interrupted BOOLEAN);
CREATE OR REPLACE TABLE otel_events (
  ts TIMESTAMP, event VARCHAR, session_id VARCHAR, prompt_id VARCHAR, qualified VARCHAR,
  trigger VARCHAR, model VARCHAR, cost_usd DOUBLE, input_tokens BIGINT, output_tokens BIGINT,
  cache_read_tokens BIGINT, cache_creation_tokens BIGINT, tool_name VARCHAR, success BOOLEAN,
  duration_ms BIGINT, plugin_name VARCHAR, plugin_version VARCHAR, tool_input VARCHAR,
  result_bytes BIGINT);
"""


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def _attr_value(v: dict[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key in v:
            return int(v[key]) if key == "intValue" else v[key]
    return None


def _num(v: Any, cast: type = float) -> Any:
    try:
        return cast(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def iter_hook_events(path: Path) -> Iterator[tuple[Any, ...]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:  # a torn or hand-edited line must not break reporting
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            row = {**e, "ts": _ts(e.get("ts"))}
        except (ValueError, TypeError, AttributeError):
            continue
        yield tuple(row.get(c) for c in HOOK_COLS)


def iter_otel_events(path: Path) -> Iterator[tuple[Any, ...]]:
    """Flatten OTLP ExportLogsServiceRequest lines into one row per claude_code.* log record."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict):
            continue
        for rl in req.get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                for rec in sl.get("logRecords", []):
                    try:
                        row = _otel_row(rec)
                    except (KeyError, TypeError, ValueError, AttributeError):
                        continue  # one malformed record must not break reporting
                    yield row


def _otel_row(rec: dict[str, Any]) -> tuple[Any, ...]:
    """One otel_events row from a claude_code.* log record."""
    a = {x["key"]: _attr_value(x.get("value", {})) for x in rec.get("attributes", [])}
    name = a.get("event.name") or str(rec.get("body", {}).get("stringValue", ""))
    success = a.get("success")
    return (
        _ts(a.get("event.timestamp")),
        name.removeprefix("claude_code."),
        a.get("session.id"),
        a.get("prompt.id"),
        a.get("skill.name"),
        OTEL_TRIGGERS.get(str(a.get("invocation_trigger")), a.get("invocation_trigger")),
        a.get("model"),
        _num(a.get("cost_usd")),
        _num(a.get("input_tokens"), int),
        _num(a.get("output_tokens"), int),
        _num(a.get("cache_read_tokens"), int),
        _num(a.get("cache_creation_tokens"), int),
        a.get("tool_name"),
        None if success is None else str(success).lower() == "true",
        _num(a.get("duration_ms"), int),
        a.get("plugin.name"),
        a.get("plugin.version"),
        str(a["tool_input"])[:300] if a.get("tool_input") else None,
        _num(a.get("tool_result_size_bytes"), int),
    )


def connect(events_file: Path, otel_logs_file: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with both tables loaded."""
    con = duckdb.connect()
    con.execute(DDL)
    hooks = list(iter_hook_events(events_file))
    otel = list(iter_otel_events(otel_logs_file))
    if hooks:
        con.executemany(
            f"INSERT INTO hook_events VALUES ({', '.join('?' * len(HOOK_COLS))})", hooks
        )
    if otel:
        con.executemany(f"INSERT INTO otel_events VALUES ({', '.join('?' * len(OTEL_COLS))})", otel)
    return con
