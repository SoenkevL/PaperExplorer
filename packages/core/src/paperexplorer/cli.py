"""The `paperexplorer` command line; pipeline commands attach here."""

from pathlib import Path
from typing import Annotated

import typer

from paperexplorer import __version__, citations, parsing
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


@app.command("parse")
def parse_command(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="A PDF to parse.")],
    show: Annotated[int, typer.Option(help="How many weak Citations to list.")] = 10,
) -> None:
    """Parse a PDF and report its blocks, References and how well its Citations are linked."""
    work = parsing.parse(pdf, Settings())
    graph = citations.extract(work)
    origin = "OCR Text (a scan)" if work.text_origin is parsing.TextOrigin.OCR else "printed text"
    headings = sum(b.kind is parsing.BlockKind.HEADING for b in work.blocks)
    links = [c.link for c in graph.citations]
    certain = sum(1 for link in links if link and link.confidence >= 1.0)
    high = sum(1 for link in links if link and citations.LOW_CONFIDENCE <= link.confidence < 1.0)
    typer.echo(f"{pdf.name}: {origin}")
    typer.echo(f"Blocks: {len(work.blocks)} ({headings} headings)")
    typer.echo(f"References: {len(graph.references)}")
    typer.echo(
        f"Citations: {len(graph.citations)} (certain {certain}, high {high}, "
        f"low {len(graph.low_confidence())}, unlinked {len(graph.unlinked())})"
    )
    weak = graph.unlinked() + graph.low_confidence()
    for c in weak[:show]:
        state = "unlinked" if c.link is None else f"low {c.link.confidence:.1f}"
        typer.echo(
            f"  p{work.blocks[c.block].page} {state}: {c.text!r} in {c.citing_sentence[:80]!r}"
        )
