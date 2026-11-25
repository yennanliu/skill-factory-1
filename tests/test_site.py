"""The project site (site/) is plain static HTML: check it stays self-consistent."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(str(a["id"]))
        for key in ("href", "src"):
            if a.get(key):
                self.hrefs.append(str(a[key]))


def parsed() -> _Links:
    p = _Links()
    p.feed((SITE / "index.html").read_text())
    return p


def test_in_page_anchors_resolve() -> None:
    p = parsed()
    for h in p.hrefs:
        if h.startswith("#"):
            assert h[1:] in p.ids, h


def test_local_files_exist() -> None:
    for h in parsed().hrefs:
        if not re.match(r"^(https?:|#|data:|mailto:)", h):
            assert (SITE / h).is_file(), h


def test_repo_doc_links_point_at_real_files() -> None:
    prefix = "https://github.com/yennanliu/skill-factory-1/blob/main/"
    for h in parsed().hrefs:
        if h.startswith(prefix):
            assert (ROOT / h.removeprefix(prefix)).is_file(), h


def test_commands_on_the_site_exist() -> None:
    from typer.main import get_command

    from skill_factory.cli import app

    cli = get_command(app)
    known = set(cli.commands)  # type: ignore[attr-defined]
    html = (SITE / "index.html").read_text()
    for cmd in set(re.findall(r"<code>sf ([a-z]+)", html)):
        assert cmd in known, f"site mentions `sf {cmd}`, which doesn't exist"
