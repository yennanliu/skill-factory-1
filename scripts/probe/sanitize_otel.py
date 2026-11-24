#!/usr/bin/env python3
"""Sanitize one OTLP JSONL file (paths, emails, ids, account attributes) for committing.

usage: sanitize_otel.py <file.jsonl> <tmp_root>   (writes to stdout)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

src, tmp_root = Path(sys.argv[1]), sys.argv[2]
home = str(Path.home())
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# attribute keys whose values identify the user/org/machine
REDACT_KEYS = {
    "user.email",
    "user.account_uuid",
    "user.id",
    "user.account_id",
    "organization.id",
    "host.name",
    "host.arch",
    "os.version",
    "terminal.type",
    "service.instance.id",
    "anthropic.plan",
    "anthropic.seat_type",
    "request_id",
    "client_request_id",
    "server_name",
}
# events that only describe the user's own environment (connectors, managed settings)
DROP_EVENTS = {"claude_code.mcp_server_connection", "claude_code.managed_settings_resolved"}
TOOLU = re.compile(r"toolu_[A-Za-z0-9]+")
ids: dict[str, str] = {}


def scrub(s: str) -> str:
    for real in sorted({tmp_root, str(Path(tmp_root).resolve())}, key=len, reverse=True):
        s = s.replace(real, "<TMP>")
    s = s.replace(home, "<HOME>")
    s = EMAIL.sub("<EMAIL>", s)
    s = UUID.sub(lambda m: ids.setdefault(m.group(0), f"<UUID{len(ids)}>"), s)
    return TOOLU.sub(lambda m: ids.setdefault(m.group(0), f"<TOOLU{len(ids)}>"), s)


def walk(o: Any) -> Any:
    if isinstance(o, dict):
        if "logRecords" in o:
            o = {
                **o,
                "logRecords": [
                    r
                    for r in o["logRecords"]
                    if r.get("body", {}).get("stringValue") not in DROP_EVENTS
                ],
            }
        if o.get("key") in REDACT_KEYS and "value" in o:
            return {"key": o["key"], "value": {"stringValue": "<redacted>"}}
        return {k: walk(v) for k, v in o.items()}
    if isinstance(o, list):
        return [walk(v) for v in o]
    if isinstance(o, str):
        return scrub(o)
    return o


for line in src.read_text().splitlines():
    if line.strip():
        print(json.dumps(walk(json.loads(line)), sort_keys=True))
