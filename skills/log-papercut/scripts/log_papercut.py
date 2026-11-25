#!/usr/bin/env python3
"""Append one structured papercut report to $SF_HOME/papercuts/inbox.jsonl. Standard library only.

usage: log_papercut.py --skill NAME --tried TEXT --expected TEXT --happened TEXT
                       [--suggested-fix TEXT] [--severity low|med|high] [--skill-version X]

Each text field is cut to 500 characters. Never pass secrets, prompt text or file contents.
Always exits 0: a failed report must not derail the user's task (the JSON is echoed instead).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

MAX_LEN = 500


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="File a papercut report.")
    ap.add_argument("--skill", required=True, help="Skill (or tool) that misbehaved.")
    ap.add_argument("--skill-version", default=None)
    ap.add_argument("--tried", required=True, help="What you were trying to do.")
    ap.add_argument("--expected", required=True, help="What you expected to happen.")
    ap.add_argument("--happened", required=True, help="What actually happened (<=3 lines of error).")
    ap.add_argument("--suggested-fix", default="", help="What would fix it, if you know.")
    ap.add_argument("--severity", choices=["low", "med", "high"], default="low")
    args = ap.parse_args(argv)

    report = {
        "v": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skill": args.skill.strip()[:100],
        "skill_version": args.skill_version,
        "tried": args.tried.strip()[:MAX_LEN],
        "expected": args.expected.strip()[:MAX_LEN],
        "happened": args.happened.strip()[:MAX_LEN],
        "suggested_fix": args.suggested_fix.strip()[:MAX_LEN],
        "severity": args.severity,
        "cwd_hash": "sha256:" + hashlib.sha256(os.getcwd().encode()).hexdigest()[:16],
    }
    report["id"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:8]
    line = json.dumps(report, separators=(",", ":"))
    home = Path(os.environ.get("SF_HOME") or Path.home() / ".skill-factory").expanduser()
    try:
        inbox = home / "papercuts" / "inbox.jsonl"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(inbox, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
        print(f"papercut {report['id']} filed for {report['skill']}")
    except OSError as e:  # e.g. a sandbox that can't write outside the workspace
        print(f"could not write the papercut inbox ({e.strerror}); report: {line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
