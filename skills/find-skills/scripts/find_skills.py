#!/usr/bin/env python3
"""Search the skill-factory catalog for skills that fit a task. Standard library only.

usage: find_skills.py "<task description>" [--limit 5]

Reads references/catalog.json next to this script's skill (written by `sf build`) and prints a
JSON list of hits, best first: skill, plugin, tier, score (0-1), goal, install. Scores below 0.15
are weak matches. Exits 0 with [] when nothing matches; exits 2 if the catalog is missing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STOP = {"the", "and", "for", "with", "this", "that", "use", "when", "asked", "into", "from",
        "your", "you", "are", "how", "what", "can", "should", "want", "need", "help", "skill",
        "skills", "there", "any", "have", "find"}
WEIGHTS = {"skill": 3.0, "triggers": 2.0, "goal": 2.0, "description": 1.0}
CATALOG = Path(__file__).resolve().parent.parent / "references" / "catalog.json"


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) >= 3 and w not in STOP:
            out.update({w, w.rstrip("s")})
    return out


def score(query: set[str], entry: dict) -> float:
    fields = {
        "skill": entry["skill"].replace("-", " "),
        "triggers": " ".join(entry.get("triggers", [])),
        "goal": entry.get("goal", ""),
        "description": entry.get("description", ""),
    }
    raw = sum(WEIGHTS[k] * len(query & tokens(v)) for k, v in fields.items())
    return raw / (len(query) * sum(WEIGHTS.values()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find skills for a task.")
    ap.add_argument("task")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args(argv)
    try:
        catalog = json.loads(CATALOG.read_text())
    except (OSError, ValueError) as e:
        print(f"catalog not found or unreadable at {CATALOG}: {e}", file=sys.stderr)
        return 2
    q = tokens(args.task)
    hits = []
    for entry in catalog.get("skills", []) if q else []:
        s = score(q, entry)
        if s > 0:
            hits.append({"skill": entry["skill"], "plugin": entry["plugin"], "tier": entry["tier"],
                         "score": round(s, 3), "goal": entry["goal"], "install": entry["install"]})
    hits.sort(key=lambda h: (-h["score"], h["skill"]))
    print(json.dumps(hits[: args.limit], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
