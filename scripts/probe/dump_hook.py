#!/usr/bin/env python3
"""Probe hook: dump the raw hook stdin payload to $SF_PROBE_OUT/hooks/. Stdlib only."""

import json
import os
import sys
import time
from pathlib import Path

event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
out = Path(os.environ.get("SF_PROBE_OUT", "/tmp/sf-probe")) / "hooks"
out.mkdir(parents=True, exist_ok=True)
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    payload = {"_unparsed": raw}
(out / f"{time.time_ns()}-{event}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
sys.exit(0)
