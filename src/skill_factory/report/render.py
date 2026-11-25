"""Render report rows to a single self-contained HTML file (inline CSS + SVG, no JS, no CDN)."""

from __future__ import annotations

from datetime import datetime
from importlib.resources import files

from jinja2 import Environment, select_autoescape

from skill_factory.report.data import SkillRow, Usage


def sparkline(values: list[int], width: int = 120, height: int = 24) -> str:
    if not values or max(values) == 0:
        return f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true"></svg>'
    top = max(values)
    step = width / max(len(values) - 1, 1)
    pts = " ".join(
        f"{i * step:.1f},{height - 2 - (v / top) * (height - 4):.1f}" for i, v in enumerate(values)
    )
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="calls per day, max {top}"><polyline points="{pts}"/></svg>'
    )


def money(v: float | None) -> str:
    return "-" if v is None else (f"${v:.4f}" if v < 0.1 else f"${v:.2f}")


def pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def render(
    rows: list[SkillRow],
    others: dict[str, Usage],
    *,
    since: datetime,
    generated: datetime,
    sources: dict[str, str],
    days: int = 14,
) -> str:
    env = Environment(autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters.update(money=money, pct=pct, sparkline=sparkline)
    template = env.from_string(files("skill_factory.report").joinpath("report.html.j2").read_text())
    total_cost = sum(r.usage.cost_total or 0 for r in rows)
    return template.render(
        rows=rows,
        others=others,
        since=since,
        generated=generated,
        sources=sources,
        total_calls=sum(r.usage.calls for r in rows),
        total_cost=total_cost,
        evaluated=sum(r.eval is not None for r in rows),
        days=days,
    )
