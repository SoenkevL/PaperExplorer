"""The `paperexplorer` command line; pipeline commands attach here."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from paperexplorer import __version__, citations, parsing, pipeline, store
from paperexplorer.citations import ConfidenceLevel, confidence_level
from paperexplorer.config import Settings
from paperexplorer.library import ZoteroLibrary
from paperexplorer.logs import configure_logging
from paperexplorer.tracing import configure_tracing

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
    levels = [confidence_level(c.link) for c in graph.citations]
    certain = levels.count(ConfidenceLevel.CERTAIN)
    high = levels.count(ConfidenceLevel.HIGH)
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


@app.command("trace-check")
def trace_check_command() -> None:
    """Send one trace to Langfuse and say where it went: the tower, or the cloud fallback."""
    tracing = configure_tracing(Settings())
    if tracing.base_url is None:
        typer.echo("Tracing is off: set PE_LANGFUSE_PUBLIC_KEY and PE_LANGFUSE_SECRET_KEY.")
        raise typer.Exit(1)
    with tracing.trace("trace-check", tags=["check"]) as trace_id:
        pass
    tracing.flush()
    typer.echo(f"Sent trace {trace_id} to {tracing.base_url}")


@app.command("migrate")
def migrate_command() -> None:
    """Bring the database schema up to date."""
    with psycopg.connect(Settings().database_url, autocommit=True) as conn:
        applied = store.migrate(conn)
    typer.echo("\n".join(f"Applied {name}" for name in applied) or "Schema is up to date")


class StageName(StrEnum):
    """The Stages `run` takes, in chain order (`pipeline.STAGES`)."""

    FETCH = "fetch"
    PARSE = "parse"
    CHUNK = "chunk"
    EXTRACT_CITATIONS = "extract_citations"


@app.command("run")
def run_command(
    stage: Annotated[StageName, typer.Argument(help="The Stage to run.")],
    work: Annotated[str, typer.Argument(help="The Work's Zotero item key, e.g. YAAC9XCX.")],
) -> None:
    """Run one Stage for one Work, recording its status; safe to run again."""
    settings = Settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        work_id = store.Works(conn).ensure(work)
        try:
            library = ZoteroLibrary.from_settings(settings)
            ctx = pipeline.StageContext(conn=conn, settings=settings, library=library)
            result = pipeline.run_stage(ctx, stage.value, work_id)
        except Exception as e:
            typer.echo(f"{stage.value} failed for {work}: {e}", err=True)
            raise typer.Exit(1) from e
    counts = ", ".join(f"{k} {v}" for k, v in result.counts.items())
    typer.echo(f"{stage.value} {work}: {counts}")
