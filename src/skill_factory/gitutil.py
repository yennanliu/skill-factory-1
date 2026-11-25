"""Small git helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


def changed_files(root: Path, base: str | None = None) -> list[str]:
    """Paths changed vs `base` (default: HEAD, including staged, unstaged and untracked)."""

    def git(*args: str) -> list[str]:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        return [line for line in out.splitlines() if line]

    # Working-tree changes vs HEAD (staged + unstaged) and untracked files...
    files = set(git("diff", "--name-only", "HEAD"))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    if base:
        # ...plus what this branch changed since it left `base` (three dots: changes that
        # landed on `base` meanwhile are not ours).
        files |= set(git("diff", "--name-only", f"{base}...HEAD"))
    return sorted(files)


def changed_skills(root: Path, base: str | None = None) -> list[str]:
    names = {
        parts[1]
        for f in changed_files(root, base)
        if (parts := Path(f).parts)[:1] == ("skills",) and len(parts) > 2
    }
    return sorted(n for n in names if (root / "skills" / n).is_dir())
