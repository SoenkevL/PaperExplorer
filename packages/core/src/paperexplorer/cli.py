"""The `paperexplorer` command line; pipeline commands attach here."""

import asyncio
import json
import textwrap
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import psycopg
import typer
from psycopg.rows import TupleRow

from paperexplorer import (
    __version__,
    asking,
    citations,
    evals,
    parsing,
    pipeline,
    resolution,
    retrieval,
    store,
)
from paperexplorer.citations import ConfidenceLevel, confidence_level
from paperexplorer.config import Settings
from paperexplorer.domain import Answer, Level, Resolution, Scope, Work
from paperexplorer.library import ZoteroLibrary, sync
from paperexplorer.logs import configure_logging
from paperexplorer.tracing import configure_tracing, trace_release
from paperexplorer.wiring import Wiring

app = typer.Typer(no_args_is_help=True, help="Follow a paper's citations into its sources.")


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log at DEBUG.")] = False,
) -> None:
    """Configure logging from settings before any command runs."""
    settings = Settings()
    configure_logging("DEBUG" if verbose else settings.log_level, settings.log_format)


def _wiring(ctx: typer.Context, settings: Settings) -> Wiring:
    """The command's adapters: from `settings`, unless the caller's `obj` builds them."""
    build: Callable[[Settings], Wiring] = ctx.obj or Wiring.from_settings
    return build(settings)


@contextmanager
def _work_or_exit(
    settings: Settings, key: str
) -> Generator[tuple[psycopg.Connection[TupleRow], Work]]:
    """A store connection, its schema up to date, and the Work `key`; exit 1 if it is unknown."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        found = store.Works(conn).find(key)
        if found is None:
            typer.echo(f"No Work {key}; run fetch, parse, chunk and embed first", err=True)
            raise typer.Exit(1)
        yield conn, found


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
    EMBED = "embed"
    EXTRACT_CITATIONS = "extract_citations"
    RESOLVE = "resolve"


@app.command("run")
def run_command(
    ctx: typer.Context,
    stage: Annotated[StageName, typer.Argument(help="The Stage to run.")],
    work: Annotated[str, typer.Argument(help="The Work's Zotero item key, e.g. YAAC9XCX.")],
) -> None:
    """Run one Stage for one Work, recording its status; safe to run again."""
    settings = Settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        work_id = store.Works(conn).ensure(work)
        wiring = None
        try:
            wiring = _wiring(ctx, settings)
            stage_ctx = pipeline.StageContext(
                conn=conn,
                settings=settings,
                library=ZoteroLibrary.from_settings(settings),
                embedder=wiring.embedder,
                tracing=wiring.tracing,
            )
            result = pipeline.run_stage(stage_ctx, stage.value, work_id)
        except Exception as e:
            typer.echo(f"{stage.value} failed for {work}: {e}", err=True)
            raise typer.Exit(1) from e
        finally:
            if wiring is not None:
                wiring.tracing.flush()
    counts = ", ".join(f"{k} {v}" for k, v in result.counts.items())
    typer.echo(f"{stage.value} {work}: {counts}")


def _items(n: int) -> str:
    return f"{n} Library Item{'' if n == 1 else 's'}"


@app.command("sync")
def sync_command() -> None:
    """Read the whole Zotero Library again into the Works; `resolve` syncs only its changes."""
    settings = Settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        with conn.transaction():
            result = sync(conn, ZoteroLibrary.from_settings(settings), full=True)
    if result.stale:
        typer.echo("Zotero could not be reached; the last copy of the Library stands", err=True)
        raise typer.Exit(1)
    typer.echo(f"Synced: {_items(result.items)} recorded, {result.removed} Works removed")


def _key(work: Work | None) -> str:
    if work is None:
        return "-"
    return work.zotero_key or f"#{work.id}"


@app.command("duplicates")
def duplicates_command(
    stand: Annotated[
        str | None,
        typer.Option("--stand", metavar="KEY", help="Let this Duplicate Item stand for its DOI."),
    ] = None,
    merge: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--merge",
            metavar="KEEP OTHER",
            help="Merge OTHER into KEEP in Zotero, as Zotero desktop does; OTHER to the trash.",
        ),
    ] = None,
) -> None:
    """List each Work's Duplicate Items, its Standing Item first; let another stand, or merge."""
    settings = Settings()
    library = ZoteroLibrary.from_settings(settings)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        works = store.Works(conn)
        with conn.transaction():
            sync(conn, library)
        if stand is not None:
            standing = _duplicate_item(works, stand)
            with conn.transaction():
                works.let_stand(standing.id)
            typer.echo(f"{stand} now stands for {standing.doi}")
            return
        if merge is not None:
            keep, other = (_duplicate_item(works, key) for key in merge)
            if keep.doi != other.doi or keep.id == other.id:
                typer.echo(f"{keep.zotero_key} and {other.zotero_key} do not share a DOI", err=True)
                raise typer.Exit(1)
            typer.confirm(
                f"Merge {other.zotero_key} into {keep.zotero_key} in Zotero? {keep.zotero_key}"
                f" takes its collections, tags, notes and attachments and keeps its own fields;"
                f" {other.zotero_key} goes to Zotero's trash.",
                abort=True,
            )
            assert keep.zotero_key is not None and other.zotero_key is not None
            library.merge(keep.zotero_key, other.zotero_key)
            with conn.transaction():
                result = sync(conn, library)  # follows dc:replaces, moving the Resolutions
            typer.echo(
                f"Merged {other.zotero_key} into {keep.zotero_key};"
                f" {result.moved} Resolutions moved"
            )
            return
        groups = works.duplicate_items()
    if not groups:
        typer.echo("No Duplicate Items: no DOI is held by two Library Items")
    for group in groups:
        typer.echo(group[0].doi)
        for i, w in enumerate(group):
            added = w.added.date().isoformat() if w.added else "-"
            pdf = "pdf" if w.pdf else "-"
            typer.echo(f"  {'*' if i == 0 else ' '} {_key(w):<10} {pdf:<3} {added:<10} {w.title}")


def _duplicate_item(works: store.Works, key: str) -> Work:
    """The Work of a Duplicate Item; else exit with why."""
    work = works.find(key)
    if (
        work is None
        or work.doi is None
        or all(work.id not in {w.id for w in g} for g in works.duplicate_items())
    ):
        typer.echo(f"{key} is not a Duplicate Item: no other Library Item holds its DOI", err=True)
        raise typer.Exit(1)
    return work


def _state(r: Resolution) -> str:
    return r.level.value if r.level is not None else r.state.value


@app.command("resolutions")
def resolutions_command(
    work: Annotated[str, typer.Argument(help="The Work's Zotero item key, e.g. YAAC9XCX.")],
    low: Annotated[
        bool, typer.Option("--low", help="Only low-confidence ones and DOI Conflicts.")
    ] = False,
) -> None:
    """List each Reference of a Work with what it resolved to, as the last `resolve` left it."""
    settings = Settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        store.migrate(conn)
        works = store.Works(conn)
        found = works.find(work)
        if found is None:
            typer.echo(f"No Work {work}; sync the Library or run a Stage for it first", err=True)
            raise typer.Exit(1)
        resolutions = store.Resolutions(conn).for_work(found.id)
        parsed = store.WorkTexts(conn).get(found.id)
        printed = {r.id: r.text for r in parsed.references} if parsed else {}
        ids = {i for r in resolutions for i in (r.work, r.doi_conflict) if i is not None}
        named = {i: works.get(i) for i in ids}
    if not resolutions:
        typer.echo(f"No Resolutions for {work} yet; run `paperexplorer run resolve {work}`")
        return
    counts = resolution.counts(resolutions)
    del counts["references"]
    summary = ", ".join(
        f"{n} {name.replace('doi_conflicts', 'DOI Conflicts')}" for name, n in counts.items()
    )
    typer.echo(f"{len(resolutions)} References of {work}: {summary}")
    for r in resolutions:
        if low and r.level is not Level.LOW and r.doi_conflict is None:
            continue
        conflict = (
            f"  [DOI Conflict: {_key(named[r.doi_conflict])}]" if r.doi_conflict is not None else ""
        )
        work_key = _key(named[r.work]) if r.work is not None else "-"
        text = textwrap.shorten(printed.get(r.reference, ""), 70)
        typer.echo(f"{r.reference:<6} {_state(r):<10} {work_key:<10} {text}{conflict}")


def _placing(placing: retrieval.Placing | None) -> str:
    return f"{placing.rank:>2} {placing.score:.3f}" if placing else " -     -"


@app.command("search")
def search_command(
    ctx: typer.Context,
    work: Annotated[str, typer.Argument(help="The Work's Zotero item key, e.g. YAAC9XCX.")],
    query: Annotated[str, typer.Argument(help="What to search for, e.g. a Question.")],
    k: Annotated[int, typer.Option("-k", min=1, help="How many Chunks to list.")] = 8,
    show_text: Annotated[
        bool, typer.Option("--text/--no-text", help="Print the start of each Chunk's text.")
    ] = True,
) -> None:
    """Search a Work's Chunks and list them best first, with both rankings and their RRF."""
    settings = Settings()
    with _work_or_exit(settings, work) as (conn, found):
        wiring = _wiring(ctx, settings)
        try:
            with wiring.tracing.trace(
                "search", tags=["cli", f"work:{work}"], metadata={"work_id": str(found.id)}
            ):
                results = retrieval.search(conn, wiring.embedder, query, Scope.of(found.id), k=k)
        finally:
            wiring.tracing.flush()
    chunks = "Chunk" if len(results) == 1 else "Chunks"
    typer.echo(f"{work}: {len(results)} {chunks} for {query!r} ({wiring.embedder.model})")
    for i, r in enumerate(results, start=1):
        heading = " > ".join(r.chunk.heading_path) or "(no heading)"
        typer.echo(
            f"{i:>2}  rrf {r.rrf:.4f}  vector {_placing(r.vector)}  "
            f"full text {_placing(r.full_text)}  p{r.chunk.page}  {heading}"
        )
        if show_text:
            typer.echo(textwrap.indent(textwrap.shorten(r.chunk.text, 240), "     "))


@app.command("ask")
def ask_command(
    ctx: typer.Context,
    work: Annotated[str, typer.Argument(help="The Work's Zotero item key, e.g. YAAC9XCX.")],
    question: Annotated[str, typer.Argument(help="The Question to ask about it.")],
) -> None:
    """Ask a Work a Question: stream the model's draft, then list its Pointers and Quotes."""
    settings = Settings()
    with _work_or_exit(settings, work) as (conn, found):
        wiring = _wiring(ctx, settings)
        conversation = asking.Conversation(work=found)
        try:
            started = time.monotonic()
            answered = asyncio.run(_stream_answer(wiring.asking(conn), conversation, question))
            latency = time.monotonic() - started
        finally:
            wiring.tracing.flush()
        blocks = [b.text for b in (store.WorkTexts(conn).get(found.id) or _NO_TEXT).blocks]
    _print_answer(answered.answer, conversation, blocks)
    counts = answered.answer.counts()
    coverage = "-" if counts.pointer_coverage is None else f"{counts.pointer_coverage:.2f}"
    typer.echo(
        f"summary: {answered.reading.value},{' redrafted,' if answered.redrafted else ''}"
        f" sentences {counts.sentences},"
        f" substantive {counts.substantive_sentences},"
        f" pointer coverage {coverage}, close {counts.pointers_close},"
        f" weak {counts.pointers_weak}, quotes exact {counts.quotes_exact},"
        f" not exact {counts.quotes_not_exact}, unknown handles {counts.unknown_handles},"
        f" model requests {answered.model_requests}, tool calls {answered.tool_calls},"
        f" latency {latency:.1f} s"
    )


_NO_TEXT = parsing.ParsedWork(text_origin=parsing.TextOrigin.PRINTED, blocks=[], references=[])


@app.command("eval")
def eval_command(
    ctx: typer.Context,
    work: Annotated[str, typer.Argument(help="The Seed's Zotero item key, e.g. YAAC9XCX.")],
    questions: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="The Question set to ask.")
    ] = Path("evals/seed-questions.yaml"),
    retrieval_mode: Annotated[
        bool,
        typer.Option(
            "--retrieval/--no-retrieval",
            help="Also ask by retrieval, forced: the fallback for a Seed too large to read whole.",
        ),
    ] = True,
    only: Annotated[
        list[str] | None, typer.Option(help="Ask only this Question, by name; repeatable.")
    ] = None,
    repeat: Annotated[
        int,
        typer.Option(
            min=1,
            help="Ask each Question live this many times, each in a fresh Conversation;"
            " retrieval asks it once.",
        ),
    ] = 1,
    run_id: Annotated[
        str | None, typer.Option(help="The run's id; else its start in UTC and the commit.")
    ] = None,
    summary_dir: Annotated[Path, typer.Option(help="Where the summary to commit goes.")] = Path(
        "evals/runs"
    ),
) -> None:
    """Ask the Seed its eval Questions as the reader does, and by retrieval; report both.

    The live mode asks each Question `--repeat` times, retrieval once; the bar pools the live
    turns and shows each repeat beside them. Traces go to Langfuse's `eval` environment. The
    full report, Answers and all, goes to `<PE_DATA_DIR>/eval/<run id>/`; the summary, without
    the Seed's or the Answers' words, also to `--summary-dir`, to be committed.
    """
    settings = evals.eval_settings()
    dataset = evals.load_questions(questions)
    if only:
        missing = sorted(set(only) - {c.name for c in dataset.cases})
        if missing:
            typer.echo(f"No such Question in {questions}: {', '.join(missing)}", err=True)
            raise typer.Exit(1)
        dataset.cases = [c for c in dataset.cases if c.name in only]
    modes = [evals.Mode.LIVE, *([evals.Mode.RETRIEVAL] if retrieval_mode else [])]
    with _work_or_exit(settings, work) as (conn, found):
        chunks = store.Chunks(conn)
        stored = chunks.for_work(found.id)
        if not stored or chunks.without_embedding(found.id, settings.embedding_model):
            typer.echo(f"{work} is not chunked and embedded; run chunk and embed first", err=True)
            raise typer.Exit(1)
        unknown = evals.unknown_places(dataset, stored)
        if unknown:
            typer.echo(f"No Chunk of {work} sits at these places of {questions}:", err=True)
            for name, place in unknown:
                typer.echo(f"  {name}: {place}", err=True)
            raise typer.Exit(1)
        wiring = _wiring(ctx, settings)
        info = evals.run_info(
            settings,
            found,
            questions,
            embedding_model=wiring.embedder.model,
            git_sha=trace_release(settings),
            tracing_host=wiring.tracing.base_url,
            started=datetime.now(UTC),
            run_id=run_id,
            repeat=repeat,
        )
        asked = len(dataset.cases)
        named = [
            f"{m.value} {repeat} times" if m is evals.Mode.LIVE and repeat > 1 else m.value
            for m in modes
        ]
        typer.echo(
            f"eval {work}: {asked} Question{'s' if asked != 1 else ''},"
            f" {' and '.join(named)}, run {info.run_id}"
        )
        try:
            report = asyncio.run(evals.run_eval(wiring.asking(conn), found, dataset, info, modes))
        finally:
            wiring.tracing.flush()
    summary = evals.summarize(report)
    written = evals.write_run(report, summary, settings.data_dir / "eval", summary_dir)
    for check in summary.bar:
        met = {True: "met", False: "NOT met", None: "to judge"}[check.met]
        value = check.value
        shown = "-" if value is None else f"{value:.2f}" if isinstance(value, float) else value
        typer.echo(f"{check.name} {shown} ({check.bar}): {met}")
    typer.echo(f"full report: {written.run_dir}")
    typer.echo(f"summary to commit: {', '.join(str(p) for p in written.committed)}")


async def _stream_answer(
    ctx: asking.AskingContext, conversation: asking.Conversation, question: str
) -> asking.AnsweredEvent:
    """Print each step and the draft as they come; the Answer."""
    line_open = False
    async for event in asking.ask(ctx, conversation, question):
        match event:
            case asking.StepEvent(tool=tool, args=args):
                if line_open:
                    typer.echo()
                typer.echo(f"→ {tool} {json.dumps(args, ensure_ascii=False)}")
                line_open = False
            case asking.DeltaEvent(pieces=pieces):
                text = "".join(
                    p.text if isinstance(p, asking.TextPiece) else f"[#{p.handle}]" for p in pieces
                )
                typer.echo(text, nl=False)
                line_open = not text.endswith("\n")
            case asking.RedraftEvent():
                if line_open:
                    typer.echo()
                typer.echo("→ redraft: the draft gave no handle, so it is written again")
                line_open = False
            case asking.AnsweredEvent():
                if line_open:
                    typer.echo()
                return event
            case asking.ErrorEvent(message=message):
                if line_open:
                    typer.echo()
                typer.echo(f"ask failed: {message}", err=True)
                raise typer.Exit(1)
    raise RuntimeError("ask ended without an Answer or an error")


def _print_answer(answer: Answer, conversation: asking.Conversation, blocks: list[str]) -> None:
    """The Answer's text, each sentence's Pointers with their places, and its Quotes."""
    typer.echo(f"\nAnswer\n{answer.text}\n")
    chunks = {c.id: c for c in conversation.handles.values()}
    typer.echo("Pointers" if any(s.pointers for s in answer.sentences) else "Pointers: none")
    for i, sentence in enumerate(answer.sentences, start=1):
        for p in sentence.pointers:
            chunk = chunks[p.chunk_id].chunk
            heading = " > ".join(chunk.heading_path) or "(no heading)"
            typer.echo(f"  s{i}  {p.support.value}  {p.similarity:.2f}  p{chunk.page}  {heading}")
            typer.echo(f"      {blocks[p.block_id][p.start : p.end]}")
    typer.echo("Quotes" if answer.quotes else "Quotes: none")
    for q in answer.quotes:
        words = answer.text[q.start : q.end]
        if q.exact and q.block_id is not None:
            page = next(c.chunk.page for c in chunks.values() if c.chunk.block == q.block_id)
            typer.echo(f'  "{words}"  exact  p{page}')
        else:
            typer.echo(f'  "{words}"  not exact')
