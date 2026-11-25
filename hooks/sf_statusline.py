#!/usr/bin/env python3
"""skill-factory status line for Claude Code. Standard library only; prints one line.

Claude Code pipes session JSON on stdin (model, cost, context window, session id). Output:

    $0.42 · Opus 5.5 · ctx 31% · lc-add

The cost turns yellow at 50% and red at 80% of the budget, and gets a "!" at 100%. The budget is
`session_budget_usd` in $SF_HOME/config.json (no budget: no colour). The last skill comes from
the tail of $SF_HOME/telemetry/events.jsonl for this session. There are no hard caps; this only
shows the cost.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

YELLOW, RED, RESET = "\033[33m", "\033[31m", "\033[0m"
TAIL_BYTES = 64 * 1024


def sf_home() -> Path:
    return Path(os.environ.get("SF_HOME") or Path.home() / ".skill-factory").expanduser()


def budget() -> float | None:
    try:
        value = json.loads((sf_home() / "config.json").read_text()).get("session_budget_usd")
        return float(value) if value else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def last_skill(session_id: str) -> str | None:
    path = sf_home() / "telemetry" / "events.jsonl"
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("session_id") == session_id and e.get("event") == "skill_invoked":
            return str(e.get("skill") or "") or None
    return None


def colour_cost(cost: float, limit: float | None, use_colour: bool) -> str:
    text = f"${cost:.2f}"
    if not limit:
        return text
    ratio = cost / limit
    if ratio >= 1:
        text += "!"
    if not use_colour:
        return text
    if ratio >= 0.8:
        return f"{RED}{text}{RESET}"
    if ratio >= 0.5:
        return f"{YELLOW}{text}{RESET}"
    return text


def render(data: dict[str, Any], use_colour: bool = True) -> str:
    parts = []
    cost = (data.get("cost") or {}).get("total_cost_usd")
    if isinstance(cost, int | float):
        parts.append(colour_cost(float(cost), budget(), use_colour))
    model = (data.get("model") or {}).get("display_name") or (data.get("model") or {}).get("id")
    if model:
        parts.append(str(model))
    used = (data.get("context_window") or {}).get("used_percentage")
    if isinstance(used, int | float):
        parts.append(f"ctx {used:.0f}%")
    skill = last_skill(str(data.get("session_id") or ""))
    if skill:
        parts.append(skill)
    return " · ".join(parts)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        print(
            render(
                data if isinstance(data, dict) else {}, use_colour=not os.environ.get("NO_COLOR")
            )
        )
    except Exception:
        print("")


if __name__ == "__main__":
    main()
