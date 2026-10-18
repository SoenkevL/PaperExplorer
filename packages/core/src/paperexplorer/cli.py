"""The `paperexplorer` command line; pipeline commands attach here."""

from typing import Annotated

import typer

from paperexplorer import __version__
from paperexplorer.config import Settings
from paperexplorer.logs import configure_logging

app = typer.Typer(no_args_is_help=True, help="Follow a paper's citations into its sources.")


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log at DEBUG.")] = False,
) -> None:
    """Configure logging from settings before any command runs."""
    settings = Settings()
    configure_logging("DEBUG" if verbose else settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)
