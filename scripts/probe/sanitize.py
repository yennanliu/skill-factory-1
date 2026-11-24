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
import sys
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
            return {
                k: ("<redacted>" if k in BLANK_KEYS else self.obj(v))
                for k, v in o.items()
                if k not in DROP_KEYS
            }
        if isinstance(o, list):
            return [self.obj(v) for v in o]
        if isinstance(o, str):
            return self.text(o)
        return o


for case in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
    sc = Scrubber()
    out = dest_dir / case.name
    (out / "hooks").mkdir(parents=True, exist_ok=True)
    for old in (out / "hooks").glob("*.json"):
        old.unlink()
    for i, src in enumerate(sorted((case / "hooks").glob("*.json"))):
        event = src.stem.split("-", 1)[1]
        data = sc.obj(json.loads(src.read_text()))
        (out / "hooks" / f"{i:02d}-{event}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n"
        )
    lines = []
    for raw in (case / "stream.jsonl").read_text().splitlines():
        if raw.strip():
            lines.append(json.dumps(sc.obj(json.loads(raw)), sort_keys=True))
    (out / "stream.jsonl").write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")
