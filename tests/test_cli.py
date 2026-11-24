from typer.testing import CliRunner

from skill_factory import __version__
from skill_factory.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output


def test_version_prints_sf_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"sf {__version__}" in result.output
