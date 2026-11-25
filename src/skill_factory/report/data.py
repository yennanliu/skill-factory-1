"""Gather everything the report shows, per skill. Pure data; rendering lives in render.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from skill_factory.spec import SkillDir
from skill_factory.telemetry.stats import SkillStats, skill_stats


@dataclass
class Usage:
    calls: int = 0
    model_calls: int = 0
    user_calls: int = 0
    sessions: int = 0
    repos: int = 0  # distinct working-directory hashes (hook events)
    active_weeks: int = 0
    cost_total: float | None = None
    cost_mean: float | None = None  # per activation (OTel)
    cost_p90: float | None = None
    requests_mean: float | None = None  # API requests per activation, a proxy for turns
    tool_errors: int = 0
    versions: list[str] = field(default_factory=list)  # plugin versions seen (OTel plugin_loaded)
    co_used: list[tuple[str, int]] = field(default_factory=list)  # other skills in same sessions
    daily: list[int] = field(default_factory=list)  # calls per day over the window, oldest first
    source: str = "-"

    @property
    def retained(self) -> bool:
        return self.active_weeks >= 2


@dataclass
class EvalSnapshot:
    when: str
    status: str
    model: str | None
    pass_rate: float | None
    interval: tuple[float, float]
    baseline_pass_rate: float | None
    runs: int
    cost_usd: float
    cases: list[dict[str, Any]]

    @property
    def lift(self) -> float | None:
        if self.pass_rate is None or self.baseline_pass_rate is None:
            return None
        return self.pass_rate - self.baseline_pass_rate

    @property
    def verdict(self) -> str:
        """A first, coarse read of the with/without-skill comparison (Phase 7 refines this)."""
        lift = self.lift
        if lift is None:
            return "no baseline"
        if lift <= 0:
            return "no measurable value"
        if lift < 0.15:
            return "small lift"
        return "helps"


@dataclass
class SkillRow:
    name: str
    qualified: str
    tier: str
    plugin: str
    version: str
    owner: str
    goal: str
    always_on_tokens: int
    on_invoke_tokens: int
    target_cost_usd: float | None
    waivers: list[str]
    usage: Usage
    eval: EvalSnapshot | None

    @property
    def over_budget(self) -> bool:
        return bool(
            self.target_cost_usd
            and self.usage.cost_mean
            and self.usage.cost_mean > self.target_cost_usd
        )


def approx_tokens(text: str) -> int:
    return round(len(text) / 4)


def latest_eval(runs_dir: Path, skill: str) -> EvalSnapshot | None:
    summaries = sorted((runs_dir / skill).glob("*/summary.json"))
    if not summaries:
        return None
    path = summaries[-1]
    s = json.loads(path.read_text())
    return EvalSnapshot(
        when=path.parent.name,
        status=s["status"],
        model=s.get("model"),
        pass_rate=s.get("pass_rate"),
        interval=tuple(s.get("interval_95", [0.0, 1.0])),
        baseline_pass_rate=s.get("baseline_pass_rate"),
        runs=s.get("runs", 0),
        cost_usd=s.get("total_cost_usd", 0.0),
        cases=s.get("cases", []),
    )


ACTIVATIONS = """
WITH act AS (
  SELECT session_id, prompt_id, qualified, min(ts) AS ts
  FROM otel_events WHERE event = 'skill_activated' AND ts >= $since GROUP BY ALL),
req AS (
  SELECT session_id, prompt_id, qualified, sum(cost_usd) AS cost, count(*) AS n
  FROM otel_events WHERE event = 'api_request' AND qualified IS NOT NULL AND ts >= $since
  GROUP BY ALL)
SELECT a.qualified, avg(r.cost), quantile_cont(r.cost, 0.9), avg(r.n)
FROM act a LEFT JOIN req r USING (session_id, prompt_id, qualified)
GROUP BY 1
"""

CALL_EVENTS = """
SELECT qualified, session_id, ts, NULL AS cwd_hash FROM otel_events
  WHERE event = 'skill_activated' AND ts >= TIMESTAMP '{since}'
UNION ALL
SELECT qualified, session_id, ts, cwd_hash FROM hook_events
  WHERE event = 'skill_invoked' AND ts >= TIMESTAMP '{since}'
"""


def usage_by_skill(
    con: duckdb.DuckDBPyConnection, since: datetime, until: datetime, days: int
) -> dict[str, Usage]:
    usage: dict[str, Usage] = {}
    for s in skill_stats(con, since):
        usage[s.skill] = _from_stats(s)

    for q, mean, p90, req in con.execute(ACTIVATIONS, {"since": since}).fetchall():
        u = usage.setdefault(q, Usage())
        u.cost_mean, u.cost_p90, u.requests_mean = mean, p90, req

    # DDL can't take bound parameters; `since` is a datetime we built, rendered as ISO text.
    view = CALL_EVENTS.format(since=since.isoformat(sep=" "))
    con.execute(f"CREATE OR REPLACE TEMP VIEW calls AS {view}")
    for q, weeks, repos in con.execute(
        "SELECT qualified, count(DISTINCT date_trunc('week', ts)), "
        "count(DISTINCT cwd_hash) FROM calls GROUP BY 1"
    ).fetchall():
        u = usage.setdefault(q, Usage())
        u.active_weeks, u.repos = weeks, repos

    start = (until - timedelta(days=days - 1)).date()
    for q, day, n in con.execute(
        "SELECT qualified, CAST(ts AS DATE), count(*) FROM calls WHERE ts >= $start GROUP BY ALL",
        {"start": start},
    ).fetchall():
        u = usage.setdefault(q, Usage())
        if not u.daily:
            u.daily = [0] * days
        idx = (day - start).days
        if 0 <= idx < days:
            u.daily[idx] += n

    for q, other, n in con.execute(
        "SELECT a.qualified, b.qualified, count(DISTINCT a.session_id) FROM calls a "
        "JOIN calls b ON a.session_id = b.session_id AND a.qualified <> b.qualified "
        "GROUP BY ALL ORDER BY 3 DESC"
    ).fetchall():
        usage.setdefault(q, Usage()).co_used.append((other, n))

    for plugin, version in con.execute(
        "SELECT DISTINCT plugin_name, plugin_version FROM otel_events "
        "WHERE event = 'plugin_loaded' AND plugin_version IS NOT NULL AND ts >= $since "
        "ORDER BY 1, 2",
        {"since": since},
    ).fetchall():
        for q, u in usage.items():
            if q.split(":", 1)[0] == plugin and version not in u.versions:
                u.versions.append(version)
    for u in usage.values():
        if not u.daily:
            u.daily = [0] * days
    return usage


def _from_stats(s: SkillStats) -> Usage:
    return Usage(
        calls=s.calls,
        model_calls=s.model_calls,
        user_calls=s.user_calls,
        sessions=s.sessions,
        cost_total=s.cost_usd,
        tool_errors=s.errors,
        source=s.source,
    )


def build_rows(
    skills: list[SkillDir], usage: dict[str, Usage], runs_dir: Path
) -> tuple[list[SkillRow], dict[str, Usage]]:
    """Rows for repo skills, plus telemetry for skills that aren't in this repo."""
    rows = []
    claimed: set[str] = set()
    for s in skills:
        qualified = f"{s.spec.plugin_name}:{s.name}"
        u = usage.get(qualified)
        if u is None:  # e.g. installed from a skills dir without the plugin prefix
            u = usage.get(s.name, Usage(daily=[]))
            claimed.add(s.name)
        claimed.add(qualified)
        rows.append(
            SkillRow(
                name=s.name,
                qualified=qualified,
                tier=s.spec.tier,
                plugin=s.spec.plugin_name,
                version=s.spec.version,
                owner=s.spec.owner,
                goal=s.spec.goal.strip(),
                always_on_tokens=approx_tokens(str(s.frontmatter.get("description", ""))),
                on_invoke_tokens=approx_tokens(s.body),
                target_cost_usd=s.spec.budget.target_cost_usd,
                waivers=list(s.spec.lint_waivers),
                usage=u,
                eval=latest_eval(runs_dir, s.name),
            )
        )
    others = {q: u for q, u in usage.items() if q not in claimed and not q.startswith("sf-eval-")}
    return rows, others
