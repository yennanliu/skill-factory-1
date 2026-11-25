#!/usr/bin/env python3
"""Rewrite probe output so it is safe to commit and stable across re-runs.

- home dir, temp dirs (and their ~/.claude/projects slug) become placeholders
- UUIDs and tool_use ids become stable placeholders (consistent within one probe case)
- thinking signatures, credential expiry and socket paths are dropped
- hook files are renamed to <seq>-<Event>.json in capture order

usage: sanitize.py <raw_dir> <dest_dir> <tmp_root>
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

raw_dir, dest_dir, tmp_root = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
home = str(Path.home())
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TOOLU = re.compile(r"toolu_[A-Za-z0-9]+")
DROP_KEYS = {"credential_expires_at", "messaging_socket_path"}
BLANK_KEYS = {"signature"}


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


TMP_FORMS = sorted({tmp_root, str(Path(tmp_root).resolve())}, key=len, reverse=True)


class Scrubber:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def _id(self, prefix: str, real: str) -> str:
        if real not in self.ids:
            n = sum(v.startswith(f"<{prefix}") for v in self.ids.values())
            self.ids[real] = f"<{prefix}{n}>"
        return self.ids[real]

    def text(self, s: str) -> str:
        for real in TMP_FORMS:
            s = s.replace(slug(real), "<TMP_SLUG>").replace(real, "<TMP>")
        s = s.replace(slug(home), "<HOME_SLUG>").replace(home, "<HOME>")
        s = UUID.sub(lambda m: self._id("UUID", m.group(0)), s)
        return TOOLU.sub(lambda m: self._id("TOOLU", m.group(0)), s)

    def obj(self, o: Any) -> Any:
        if isinstance(o, dict):
            # keys go through the same id mapping (e.g. tool_use ids used as dict keys)
            return {
                self.text(k): ("<redacted>" if k in BLANK_KEYS else self.obj(v))
                for k, v in o.items()
                if k not in DROP_KEYS
            }
        if isinstance(o, list):
            return [self.obj(v) for v in o]
        if isinstance(o, str):
            return self.text(o)
        return o


def sanitize_case(case: Path, out: Path) -> None:
    """Sanitize one probe case into `out`. Raises on incomplete or invalid captures."""
    sc = Scrubber()
    (out / "hooks").mkdir(parents=True)
    hooks = sorted((case / "hooks").glob("*.json"))
    if not hooks:
        raise ValueError(f"{case.name}: no hook payloads captured")
    for i, src in enumerate(hooks):
        event = src.stem.split("-", 1)[1]
        data = sc.obj(json.loads(src.read_text()))
        (out / "hooks" / f"{i:02d}-{event}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n"
        )
    raw_lines = (case / "stream.jsonl").read_text().splitlines()
    lines = [json.loads(raw) for raw in raw_lines if raw.strip()]
    if not lines or lines[-1].get("type") != "result":
        raise ValueError(f"{case.name}: stream.jsonl is empty or has no final result event")
    clean = [json.dumps(sc.obj(x), sort_keys=True) for x in lines]
    (out / "stream.jsonl").write_text("\n".join(clean) + "\n")


# Stage everything first; replace the destination only when every case succeeded.
staging = Path(tempfile.mkdtemp(prefix="sf-sanitize-"))
try:
    cases = sorted(p for p in raw_dir.iterdir() if p.is_dir())
    for case in cases:
        sanitize_case(case, staging / case.name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        target = dest_dir / case.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(staging / case.name, target)
        print(f"wrote {target}")
finally:
    shutil.rmtree(staging, ignore_errors=True)
