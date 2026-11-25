from __future__ import annotations

import subprocess
from pathlib import Path

from skill_factory.gitutil import changed_skills


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_changed_skills(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    assert changed_skills(repo) == []

    md = repo / "skills" / "beta" / "SKILL.md"
    md.write_text(md.read_text() + "\nmore\n")
    (repo / "market.yaml").write_text((repo / "market.yaml").read_text() + "\n")
    assert changed_skills(repo) == ["beta"]

    (repo / "skills" / "alpha" / "references").mkdir()
    (repo / "skills" / "alpha" / "references" / "new.md").write_text("x\n")  # untracked
    assert changed_skills(repo) == ["alpha", "beta"]


def test_changed_skills_with_base_ignores_upstream_changes(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    git(repo, "checkout", "-qb", "feature")
    md = repo / "skills" / "alpha" / "SKILL.md"
    md.write_text(md.read_text() + "\nfeature\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "feat")
    git(repo, "checkout", "-q", "main")
    beta = repo / "skills" / "beta" / "SKILL.md"
    beta.write_text(beta.read_text() + "\nupstream\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "up")
    git(repo, "checkout", "-q", "feature")
    assert changed_skills(repo, "main") == ["alpha"]  # beta changed only on main
