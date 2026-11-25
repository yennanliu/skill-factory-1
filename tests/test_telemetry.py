from __future__ import annotations

import gzip
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from skill_factory.telemetry.collector import serve
from skill_factory.telemetry.ingest import connect, iter_otel_events
from skill_factory.telemetry.otel import otel_env
from skill_factory.telemetry.stats import parse_since, skill_stats

ROOT = Path(__file__).resolve().parents[1]
PINNED = (ROOT / ".claude-version").read_text().strip()
OTEL_LOGS = ROOT / "tests" / "fixtures" / "otel" / PINNED / "logs.jsonl"
EPOCH = datetime(2000, 1, 1)


@pytest.fixture
def collector(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    srv = serve(tmp_path, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()


def post(url: str, body: bytes, ctype: str = "application/json", gz: bool = False) -> int:
    headers = {"Content-Type": ctype}
    if gz:
        body, headers["Content-Encoding"] = gzip.compress(body), "gzip"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def test_collector_writes_one_line_per_export(collector: tuple[str, Path]) -> None:
    url, out = collector
    assert post(f"{url}/v1/logs", b'{"resourceLogs": []}') == 200
    assert post(f"{url}/v1/logs", b'{"resourceLogs": [1]}', gz=True) == 200
    assert post(f"{url}/v1/metrics", b'{"resourceMetrics": []}') == 200
    assert (out / "logs.jsonl").read_text().splitlines() == [
        '{"resourceLogs":[]}',
        '{"resourceLogs":[1]}',
    ]
    assert (out / "metrics.jsonl").is_file()


def test_collector_rejects_protobuf_and_bad_input(collector: tuple[str, Path]) -> None:
    url, out = collector
    assert post(f"{url}/v1/logs", b"\x0a\x00", ctype="application/x-protobuf") == 415
    assert post(f"{url}/v1/logs", b"{not json") == 400
    assert post(f"{url}/v1/nope", b"{}") == 404
    assert not (out / "logs.jsonl").exists()
    with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
        assert json.load(r) == {"status": "ok"}


def test_otel_fixture_flattens_to_events() -> None:
    rows = list(iter_otel_events(OTEL_LOGS))
    names = {r[1] for r in rows}
    assert {"skill_activated", "api_request", "tool_result", "user_prompt"} <= names
    activated = [r for r in rows if r[1] == "skill_activated"]
    assert sorted(r[5] for r in activated) == ["model", "user"]  # trigger mapping


def test_stats_from_otel_fixture() -> None:
    con = connect(Path("/nonexistent"), OTEL_LOGS)
    (s,) = skill_stats(con, EPOCH)
    assert s.skill == "sf-probe:probe-echo"
    assert (s.calls, s.model_calls, s.user_calls, s.sessions, s.source) == (2, 1, 1, 2, "otel")
    expected = con.execute(
        "SELECT sum(cost_usd) FROM otel_events WHERE event='api_request' AND qualified IS NOT NULL"
    ).fetchone()
    assert expected is not None and s.cost_usd == pytest.approx(expected[0])
    assert s.cost_per_call == pytest.approx(s.cost_usd / 2)
    # the request that decided to call the skill is not attributed to it
    total = con.execute(
        "SELECT sum(cost_usd) FROM otel_events WHERE event='api_request'"
    ).fetchone()
    assert total is not None and total[0] > s.cost_usd


def test_stats_fall_back_to_hook_events(tmp_path: Path) -> None:
    ev = tmp_path / "events.jsonl"
    lines = [
        {
            "ts": "2026-11-24T10:00:00Z",
            "event": "skill_invoked",
            "session_id": "s1",
            "qualified": "p:a",
            "skill": "a",
            "plugin": "p",
            "trigger": "user",
        },
        {
            "ts": "2026-11-24T10:00:01Z",
            "event": "tool_error",
            "session_id": "s1",
            "qualified": "p:a",
            "tool": "Bash",
            "error_code": 2,
        },
        {
            "ts": "2026-11-24T10:00:02Z",
            "event": "skill_invoked",
            "session_id": "s2",
            "qualified": "p:a",
            "skill": "a",
            "plugin": "p",
            "trigger": "model",
        },
    ]
    ev.write_text("\n".join(json.dumps(x) for x in lines) + "\ntorn line {\n")
    (s,) = skill_stats(connect(ev, Path("/nonexistent")), EPOCH)
    assert (s.skill, s.calls, s.model_calls, s.user_calls, s.sessions) == ("p:a", 2, 1, 1, 2)
    assert (s.errors, s.cost_usd, s.source) == (1, None, "hooks")
    assert skill_stats(connect(ev, Path("/nonexistent")), datetime(2026, 11, 25)) == []


@pytest.mark.parametrize(("text", "hours"), [("30m", 0.5), ("12h", 12), ("1d", 24), ("2w", 336)])
def test_parse_since(text: str, hours: float) -> None:
    now = datetime(2026, 11, 24, 12)
    assert (now - parse_since(text, now)).total_seconds() == hours * 3600


def test_parse_since_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="bad --since"):
        parse_since("yesterday")


def test_otel_env_matches_profile_file() -> None:
    lines = (ROOT / "profiles" / "otel.env").read_text().splitlines()
    pairs = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    assert pairs == otel_env()


def test_errors_without_calls_still_show_and_interrupts_dont_count(tmp_path: Path) -> None:
    ev = tmp_path / "events.jsonl"
    rows = [
        {
            "ts": "2026-11-24T10:00:00Z",
            "event": "tool_error",
            "session_id": "s",
            "qualified": "p:a",
            "interrupted": False,
        },
        {
            "ts": "2026-11-24T10:00:01Z",
            "event": "tool_error",
            "session_id": "s",
            "qualified": "p:a",
            "interrupted": True,
        },
    ]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + '\n123\n{"ts": "garbage"}\n[1]\n')
    (s,) = skill_stats(connect(ev, Path("/nonexistent")), EPOCH)
    assert (s.skill, s.calls, s.errors) == ("p:a", 0, 1)


def test_malformed_otel_records_are_skipped(tmp_path: Path) -> None:
    logs = tmp_path / "logs.jsonl"
    good = {"key": "event.name", "value": {"stringValue": "skill_activated"}}
    logs.write_text(
        json.dumps(
            {
                "resourceLogs": [
                    {
                        "scopeLogs": [
                            {
                                "logRecords": [
                                    {"attributes": [{"value": {}}]},  # no "key"
                                    {"attributes": [good]},
                                ]
                            }
                        ]
                    }
                ]
            }
        )
        + "\n[]\n"
    )
    assert [r[1] for r in iter_otel_events(logs)] == ["skill_activated"]


def test_stale_pid_file_is_not_trusted(tmp_path: Path) -> None:
    import os

    from skill_factory.telemetry import otel

    (tmp_path / "collector.pid").write_text(f"{os.getpid()} 5555\n")  # alive, but not a collector
    assert otel.running(tmp_path) is None and otel.running_pid(tmp_path) is None
    assert not (tmp_path / "collector.pid").exists()
    assert otel.down(tmp_path) is False  # never kills an unrelated process
    assert otel.stored_port(tmp_path) == otel.DEFAULT_PORT


def test_up_refuses_a_port_another_collector_holds(
    tmp_path: Path, collector: tuple[str, Path]
) -> None:
    from skill_factory.telemetry import otel

    port = int(collector[0].rsplit(":", 1)[1])
    with pytest.raises(RuntimeError, match="already in use"):
        otel.up(tmp_path / "otel", port)
    assert not (tmp_path / "otel" / "collector.pid").exists()
