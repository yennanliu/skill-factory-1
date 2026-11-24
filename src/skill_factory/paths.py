"""Well-known locations used across the CLI."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    """Return the repo root: nearest parent holding `skills/` and `pyproject.toml`."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "skills").is_dir():
            return candidate
    raise FileNotFoundError(f"not inside a skill-factory repo (searched up from {here})")


def sf_home() -> Path:
    """Per-user state dir (telemetry, OTel files, papercuts). Never inside the repo."""
    return Path(os.environ.get("SF_HOME", Path.home() / ".skill-factory")).expanduser()


def claude_version_pin(root: Path) -> str | None:
    """The Claude Code version this repo's contract fixtures were captured against."""
    pin = root / ".claude-version"
    return pin.read_text().strip() if pin.is_file() else None
