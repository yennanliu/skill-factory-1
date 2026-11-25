"""`sf stats`: per-skill calls, trigger mix, cost per call and tool errors over a time window."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb

SINCE_RE = re.compile(r"^(\d+)([mhdw])$")
UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(text: str, now: datetime | None = None) -> datetime:
    m = SINCE_RE.match(text)
    if not m:
        raise ValueError(f"bad --since {text!r}; use e.g. 30m, 12h, 1d, 2w")
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return now - timedelta(**{UNITS[m.group(2)]: int(m.group(1))})


@dataclass(frozen=True)
class SkillStats:
    skill: str  # qualified name, e.g. "sf-domain-leetcode:lc-add"
    calls: int
    model_calls: int
    user_calls: int
    sessions: int
    cost_usd: float | None  # total cost of API requests made while the skill was active
    errors: int
    source: str  # where calls came from: "otel" or "hooks"

    @property
    def cost_per_call(self) -> float | None:
        return self.cost_usd / self.calls if self.cost_usd is not None and self.calls else None


QUERY = """
WITH
otel_calls AS (
  SELECT qualified, count(*) AS calls,
         count(*) FILTER (WHERE trigger = 'model') AS model_calls,
         count(*) FILTER (WHERE trigger = 'user') AS user_calls,
         count(DISTINCT session_id) AS sessions
  FROM otel_events WHERE event = 'skill_activated' AND ts >= $since GROUP BY 1),
hook_calls AS (
  SELECT qualified, count(*) AS calls,
         count(*) FILTER (WHERE trigger = 'model') AS model_calls,
         count(*) FILTER (WHERE trigger = 'user') AS user_calls,
         count(DISTINCT session_id) AS sessions
  FROM hook_events WHERE event = 'skill_invoked' AND ts >= $since GROUP BY 1),
cost AS (
  SELECT qualified, sum(cost_usd) AS cost_usd
  FROM otel_events WHERE event = 'api_request' AND qualified IS NOT NULL AND ts >= $since
  GROUP BY 1),
errors AS (
  SELECT qualified, count(*) AS errors
  FROM hook_events WHERE event = 'tool_error' AND qualified IS NOT NULL AND ts >= $since
    AND NOT coalesce(interrupted, false)  -- the user pressing Esc isn't the skill's fault
  GROUP BY 1),
names AS (
  SELECT qualified FROM otel_calls UNION SELECT qualified FROM hook_calls
  UNION SELECT qualified FROM cost UNION SELECT qualified FROM errors)
SELECT n.qualified,
       coalesce(o.calls, h.calls, 0), coalesce(o.model_calls, h.model_calls, 0),
       coalesce(o.user_calls, h.user_calls, 0), coalesce(o.sessions, h.sessions, 0),
       c.cost_usd, coalesce(e.errors, 0),
       CASE WHEN o.calls IS NOT NULL THEN 'otel' ELSE 'hooks' END
FROM names n
LEFT JOIN otel_calls o USING (qualified) LEFT JOIN hook_calls h USING (qualified)
LEFT JOIN cost c USING (qualified) LEFT JOIN errors e USING (qualified)
ORDER BY 2 DESC, 1
"""


def skill_stats(con: duckdb.DuckDBPyConnection, since: datetime) -> list[SkillStats]:
    """Calls prefer OTel `skill_activated`, falling back to hook events; cost is OTel-only."""
    rows = con.execute(QUERY, {"since": since}).fetchall()
    return [SkillStats(*r) for r in rows]
