"""A tiny local OTLP/HTTP collector (JSON encoding only), standard library only.

Claude Code exports OTel logs/metrics when started with the env in `profiles/otel.env`
(`OTEL_EXPORTER_OTLP_PROTOCOL=http/json`). Each POST body (an OTLP Export*ServiceRequest as JSON)
is appended as one line to `<dir>/{logs,metrics,traces}.jsonl`. Binds to 127.0.0.1 only: with
`OTEL_LOG_TOOL_DETAILS=1` the data includes tool parameters, so it must never leave the machine.

Run: `python -m skill_factory.telemetry.collector [--port 4318] [--dir ~/.skill-factory/otel]`
"""

from __future__ import annotations

import argparse
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SIGNALS = {"/v1/logs": "logs", "/v1/metrics": "metrics", "/v1/traces": "traces"}
DEFAULT_PORT = 4318


def make_handler(out_dir: Path) -> type[BaseHTTPRequestHandler]:
    lock = threading.Lock()
    out_dir.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "sf-otel/1"

        def log_message(self, format: str, *args: object) -> None:  # quiet
            pass

        def _reply(self, code: int, body: bytes = b"{}") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, b'{"status":"ok"}')
            else:
                self._reply(404)

        def do_POST(self) -> None:
            signal = SIGNALS.get(self.path.split("?", 1)[0])
            if signal is None:
                self._reply(404)
                return
            ctype = self.headers.get("Content-Type", "")
            if "json" not in ctype:
                msg = b'{"error":"only http/json is supported; set OTEL_EXPORTER_OTLP_PROTOCOL=http/json"}'
                self._reply(415, msg)
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            try:
                line = json.dumps(json.loads(raw), separators=(",", ":"))
            except ValueError:
                self._reply(400, b'{"error":"invalid json"}')
                return
            with lock, (out_dir / f"{signal}.jsonl").open("a") as f:
                f.write(line + "\n")
            self._reply(200)

    return Handler


def serve(out_dir: Path, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create (but don't start) the server. Call `.serve_forever()`; port 0 picks a free port."""
    return ThreadingHTTPServer((host, port), make_handler(out_dir))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--dir", type=Path, default=Path.home() / ".skill-factory" / "otel")
    args = ap.parse_args()
    srv = serve(args.dir.expanduser(), args.port)
    print(f"sf otel collector on http://127.0.0.1:{srv.server_address[1]} -> {args.dir}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
