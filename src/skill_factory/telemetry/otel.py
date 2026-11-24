"""Manage the local collector process for `sf otel up|down|status|env`."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from skill_factory.telemetry.collector import DEFAULT_PORT

# Must match profiles/otel.env (tested).
def otel_env(port: int = DEFAULT_PORT) -> dict[str, str]:
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        "OTEL_LOG_TOOL_DETAILS": "1",
    }


def _pid_file(otel_dir: Path) -> Path:
    return otel_dir / "collector.pid"


def healthy(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return bool(json.load(r).get("status") == "ok")
    except (OSError, urllib.error.URLError, ValueError):
        return False


def running_pid(otel_dir: Path) -> int | None:
    try:
        pid = int(_pid_file(otel_dir).read_text().split()[0])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None


def up(otel_dir: Path, port: int = DEFAULT_PORT) -> int:
    """Start the collector in the background; return its pid (existing one if already running)."""
    if (pid := running_pid(otel_dir)) is not None:
        return pid
    otel_dir.mkdir(parents=True, exist_ok=True)
    log = (otel_dir / "collector.log").open("a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "skill_factory.telemetry.collector", "--port", str(port),
         "--dir", str(otel_dir)],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    _pid_file(otel_dir).write_text(f"{proc.pid} {port}\n")
    for _ in range(50):
        if healthy(port):
            return proc.pid
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError(f"collector failed to start; see {otel_dir / 'collector.log'}")


def down(otel_dir: Path) -> bool:
    pid = running_pid(otel_dir)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    _pid_file(otel_dir).unlink(missing_ok=True)
    return True
