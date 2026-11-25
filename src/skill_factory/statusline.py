"""`sf statusline install`: copy the status-line script to $SF_HOME/bin and set statusLine."""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path
from typing import Any


def install_script(repo: Path, sf_home: Path) -> Path:
    dest = sf_home / "bin" / "sf_statusline.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "hooks" / "sf_statusline.py", dest)
    dest.chmod(0o755)
    return dest


def plan_settings(settings_path: Path, script: Path) -> tuple[str, str, dict[str, Any]]:
    """Return (old_text, new_text, new_settings) for adding our statusLine to a settings file."""
    old_text = settings_path.read_text() if settings_path.is_file() else "{}\n"
    data = json.loads(old_text or "{}")
    data["statusLine"] = {"type": "command", "command": f'python3 "{script}"'}
    new_text = json.dumps(data, indent=2) + "\n"
    return old_text, new_text, data


def diff(old: str, new: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (new)",
        )
    )
