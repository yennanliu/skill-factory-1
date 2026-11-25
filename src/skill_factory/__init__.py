"""skill-factory: author, evaluate, measure and improve agent skills."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skill-factory")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
