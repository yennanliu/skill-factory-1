"""Stage one skill (plus its evals/) as a throwaway plugin directory for `claude plugin eval`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from skill_factory.market.build import EXCLUDE
from skill_factory.spec import SkillDir

SHARED_DIR = "_shared"


def stage_skill(skill: SkillDir, dest: Path, skill_md: str | None = None) -> Path:
    """Create `dest/` as a plugin named `sf-eval-<skill>` holding the skill and its eval cases.

    The plugin name differs from the shipped plugins so an installed copy of the same skill can't
    shadow the one under test.
    """
    evals = skill.path / "evals"
    if not evals.is_dir() or not any(evals.iterdir()):
        raise FileNotFoundError(f"{skill.name}: no eval cases in {evals}")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".claude-plugin").mkdir(exist_ok=True)
    (dest / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": f"sf-eval-{skill.name}", "version": skill.spec.version}, indent=2)
    )
    shutil.copytree(
        skill.path,
        dest / "skills" / skill.name,
        ignore=lambda _d, names: [n for n in names if n in EXCLUDE],
    )
    if skill.spec.catalog:  # same generated catalog the built plugin ships
        from skill_factory.market.build import catalog, load_all, load_market, write_catalog

        root = skill.path.parent.parent
        write_catalog(dest / "skills" / skill.name, catalog(load_all(root), load_market(root)))
    if skill_md is not None:  # e.g. an ablation variant with one section removed
        (dest / "skills" / skill.name / "SKILL.md").write_text(skill_md)
    shutil.copytree(evals, dest / "evals", ignore=shutil.ignore_patterns("results", "__pycache__"))
    # The harness only lets a case reference files inside its own directory, so shared scaffold
    # scripts and fixtures in evals/_shared/ are copied into every case as _shared/.
    shared = evals / SHARED_DIR
    if shared.is_dir():
        for case_dir in case_dirs(dest / "evals"):
            shutil.copytree(shared, case_dir / SHARED_DIR, dirs_exist_ok=True)
    return dest


def case_dirs(evals: Path) -> list[Path]:
    """Directories the harness treats as cases (they hold case.yaml or prompt.md)."""
    found = {p.parent for pat in ("case.yaml", "prompt.md") for p in evals.rglob(pat)}
    return sorted(d for d in found if SHARED_DIR not in d.relative_to(evals).parts)
