"""`sf analyze`: find wasteful patterns in sessions (Uber's session analyzer, design doc §9.1).

Works on the OTel events Claude Code exports (otel_events in telemetry/ingest.py), not on the
undocumented transcript format. Each detector returns findings for one session.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import duckdb

READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS"}
SEARCH_TOOLS = {"Grep", "Glob"}
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

LARGE_RESULT_BYTES = 20_000
CACHE_GAP_SECONDS = 3600  # interactive sessions use a 1-hour cache TTL
CACHE_REWRITE_TOKENS = 20_000
PRELOAD_TOKENS = 30_000
SEARCH_THRASH = 8
REPEAT = 3
EASY_OUTPUT_TOKENS = 300


@dataclass(frozen=True)
class Finding:
    pattern: str
    detail: str
    fix: str


@dataclass
class Session:
    id: str
    cost: float = 0.0
    requests: int = 0
    start: datetime | None = None
    findings: list[Finding] = field(default_factory=list)


FIXES = {
    "expensive-model-easy-work": "send read-only lookups to a cheaper model (Haiku subagent)",
    "large-tool-result": "return a summary or a slice (head/grep/jq) instead of the whole output",
    "cache-expired-after-break": "start a fresh session after long breaks, or compact first",
    "heavy-preloaded-context": "trim always-on context: CLAUDE.md, always-on skills, MCP servers",
    "polling-in-main-session": "move waiting/polling into a script loop or a background agent",
    "search-thrash": "ground it: add a references/ file or lookup script with the answer",
    "retry-loop": "stop after two identical failures; file a papercut and change approach",
}


def _f(pattern: str, detail: str) -> Finding:
    return Finding(pattern, detail, FIXES[pattern])


def analyze(con: duckdb.DuckDBPyConnection, since: datetime) -> list[Session]:
    sessions: dict[str, Session] = {}
    reqs = con.execute(
        "SELECT session_id, prompt_id, ts, model, cost_usd, output_tokens, input_tokens, "
        "cache_read_tokens, cache_creation_tokens FROM otel_events "
        "WHERE event = 'api_request' AND ts >= $since AND session_id IS NOT NULL ORDER BY ts",
        {"since": since},
    ).fetchall()
    tools = con.execute(
        "SELECT session_id, prompt_id, ts, tool_name, success, tool_input, result_bytes "
        "FROM otel_events WHERE event = 'tool_result' AND ts >= $since "
        "AND session_id IS NOT NULL ORDER BY ts",
        {"since": since},
    ).fetchall()

    # A session's real first request, even when it is older than the window: a session that
    # started earlier carries its whole conversation on the first request *inside* the window.
    first_ts = dict(
        con.execute(
            "SELECT session_id, min(ts) FROM otel_events WHERE event = 'api_request' "
            "AND session_id IS NOT NULL GROUP BY 1"
        ).fetchall()
    )
    by_prompt_reqs: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    last_ts: dict[str, datetime] = {}
    for sid, pid, ts, model, cost, out_tok, in_tok, cache_read, cache_new in reqs:
        s = sessions.setdefault(sid, Session(sid))
        if s.requests == 0:
            s.start = first_ts.get(sid, ts)
        if ts == first_ts.get(sid):
            preload = (in_tok or 0) + (cache_read or 0) + (cache_new or 0)
            if preload > PRELOAD_TOKENS:
                s.findings.append(
                    _f(
                        "heavy-preloaded-context",
                        f"first request carried ~{preload:,} input tokens",
                    )
                )
        prev = last_ts.get(sid)
        if (
            prev
            and ts
            and (ts - prev).total_seconds() > CACHE_GAP_SECONDS
            and (cache_new or 0) > CACHE_REWRITE_TOKENS
        ):
            gap = (ts - prev).total_seconds() / 60
            s.findings.append(
                _f(
                    "cache-expired-after-break",
                    f"{gap:.0f} min gap, then {cache_new:,} tokens re-cached",
                )
            )
        last_ts[sid] = ts
        s.requests += 1
        s.cost += cost or 0.0
        by_prompt_reqs[(sid, pid)].append((str(model or ""), out_tok or 0))

    by_prompt_tools: dict[tuple[str, str], list[str]] = defaultdict(list)
    runs: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for sid, pid, _ts, tool, success, tool_input, size in tools:
        s = sessions.setdefault(sid, Session(sid))
        by_prompt_tools[(sid, pid)].append(str(tool))
        if size and size > LARGE_RESULT_BYTES:
            s.findings.append(_f("large-tool-result", f"{tool} returned {size / 1000:.0f} KB"))
        runs[sid].append((str(tool), tool_input or "", bool(success)))  # full stored input

    for (sid, pid), calls in by_prompt_reqs.items():
        tools_used = set(by_prompt_tools.get((sid, pid), []))
        if (
            any("opus" in m for m, _ in calls)
            and tools_used
            and tools_used <= READ_ONLY_TOOLS
            and sum(o for _, o in calls) < EASY_OUTPUT_TOKENS * len(calls)
        ):
            sessions[sid].findings.append(
                _f(
                    "expensive-model-easy-work",
                    f"Opus spent {len(calls)} requests on read-only work "
                    f"({', '.join(sorted(tools_used))})",
                )
            )

    for (sid, _pid), names in by_prompt_tools.items():
        before_edit = []
        for n in names:
            if n in EDIT_TOOLS:
                break
            before_edit.append(n)
        searches = sum(n in SEARCH_TOOLS for n in before_edit)
        if searches > SEARCH_THRASH:
            sessions[sid].findings.append(
                _f("search-thrash", f"{searches} Grep/Glob calls before the first edit")
            )

    for sid, seq in runs.items():
        # Two separate streaks of identical calls (same tool + same full input): successful
        # Bash calls (polling) and failures (retry loop). Mixed outcomes reset both.
        ok_streak = fail_streak = 1
        flagged_poll = flagged_retry = False
        for before, cur in itertools.pairwise(seq):
            same = cur[0] == before[0] and cur[1] == before[1] and cur[1] != ""
            ok_streak = ok_streak + 1 if same and cur[2] and before[2] else 1
            fail_streak = fail_streak + 1 if same and not cur[2] and not before[2] else 1
            if ok_streak >= REPEAT and cur[0] == "Bash" and not flagged_poll:
                sessions[sid].findings.append(
                    _f("polling-in-main-session", f"`{cur[1][:50]}` ran {ok_streak}x in a row")
                )
                flagged_poll = True
            if fail_streak >= REPEAT and not flagged_retry:
                sessions[sid].findings.append(
                    _f("retry-loop", f"{cur[0]} `{cur[1][:50]}` failed {fail_streak}x in a row")
                )
                flagged_retry = True

    return sorted(sessions.values(), key=lambda s: -s.cost)
