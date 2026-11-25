"""Render a LintReport as text, JSON or GitHub Actions annotations."""

from __future__ import annotations

import json
from dataclasses import asdict

from skill_factory.lint.engine import LintReport
from skill_factory.lint.rules import Finding


def _loc(f: Finding) -> str:
    return f"skills/{f.skill}/{f.file}" + (f":{f.line}" if f.line else "")


def as_text(report: LintReport) -> str:
    lines = []
    for f in sorted(report.findings, key=lambda f: (f.skill, f.severity != "error", f.rule)):
        lines.append(f"{_loc(f)}: {f.severity} {f.rule} {f.message}")
        if f.hint:
            lines.append(f"    hint: {f.hint}")
    lines.append(
        f"{len(report.skills)} skill(s): {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s), {len(report.waived)} waived"
    )
    return "\n".join(lines)


def as_json(report: LintReport) -> str:
    return json.dumps(
        {
            "skills": report.skills,
            "findings": [asdict(f) for f in report.findings],
            "waived": [asdict(f) for f in report.waived],
        },
        indent=2,
    )


def _esc_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _esc_prop(s: str) -> str:
    return _esc_data(s).replace(":", "%3A").replace(",", "%2C")


def as_github(report: LintReport) -> str:
    out = []
    for f in report.findings:
        level = "error" if f.severity == "error" else "warning"
        line = f",line={f.line}" if f.line else ""
        msg = f"{f.rule} {f.message}" + (f" — {f.hint}" if f.hint else "")
        file = _esc_prop(f"skills/{f.skill}/{f.file}")
        out.append(f"::{level} file={file}{line},title=sf lint::{_esc_data(msg)}")
    return "\n".join(out)
