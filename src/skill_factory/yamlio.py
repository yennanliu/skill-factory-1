"""Comment-preserving YAML read/write (ruamel round-trip)."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()  # round-trip mode keeps comments and key order
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    return y


def load(path: Path) -> Any:
    return _yaml().load(path.read_text())


def dump(data: Any, path: Path) -> None:
    buf = StringIO()
    _yaml().dump(data, buf)
    path.write_text(buf.getvalue())
