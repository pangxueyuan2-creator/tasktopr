from typer.testing import CliRunner

from tasktopr import __version__
from tasktopr.cli import app


def test_version_flag_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"tasktopr {__version__}"
