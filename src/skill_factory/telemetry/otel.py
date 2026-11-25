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


def _is_collector(pid: int) -> bool:
    """True if `pid` is our collector (a reused pid must never be trusted or killed)."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "skill_factory.telemetry.collector" in out.stdout


def running(otel_dir: Path) -> tuple[int, int] | None:
    """(pid, port) of the running collector; clears a stale pid file."""
    path = _pid_file(otel_dir)
    try:
        pid_s, port_s = path.read_text().split()[:2]
        pid, port = int(pid_s), int(port_s)
        os.kill(pid, 0)
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not _is_collector(pid):
        path.unlink(missing_ok=True)
        return None
    return pid, port


def running_pid(otel_dir: Path) -> int | None:
    info = running(otel_dir)
    return info[0] if info else None


def stored_port(otel_dir: Path) -> int:
    """Port of the running collector, else the default (status/env follow `up --port`)."""
    info = running(otel_dir)
    return info[1] if info else DEFAULT_PORT


def up(otel_dir: Path, port: int = DEFAULT_PORT) -> int:
    """Start the collector in the background; return its pid (existing one if already running)."""
    if (pid := running_pid(otel_dir)) is not None:
        return pid
    if healthy(port):
        # Something (e.g. a collector for another SF_HOME) already answers on this port; a new
        # one couldn't bind, and its data would silently go elsewhere.
        raise RuntimeError(f"port {port} is already in use by another collector; use --port")
    otel_dir.mkdir(parents=True, exist_ok=True)
    log = (otel_dir / "collector.log").open("a")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "skill_factory.telemetry.collector",
            "--port",
            str(port),
            "--dir",
            str(otel_dir),
        ],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    _pid_file(otel_dir).write_text(f"{proc.pid} {port}\n")
    for _ in range(50):
        if proc.poll() is not None:
            break  # our process died (e.g. bind error): a healthy port isn't ours
        if healthy(port):
            return proc.pid
        time.sleep(0.1)
    _pid_file(otel_dir).unlink(missing_ok=True)
    raise RuntimeError(f"collector failed to start; see {otel_dir / 'collector.log'}")


def down(otel_dir: Path) -> bool:
    pid = running_pid(otel_dir)  # verified to be our collector, not a reused pid
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    _pid_file(otel_dir).unlink(missing_ok=True)
    return True
