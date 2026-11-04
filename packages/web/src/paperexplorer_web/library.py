"""How a Library Item's row reads: what it shows and offers for where its Stages stand."""

from urllib.parse import urlencode

from pydantic import BaseModel

from paperexplorer_api.schemas import (
    INGESTING_STAGES,
    RESOLVING_STAGES,
    TEXT_STAGES,
    LibraryPage,
    LibraryWork,
)


class Row(BaseModel):
    """A Library Item ready to render."""

    work: LibraryWork
    message: str | None = None
    polling: bool
    start_label: str | None  # "Ingest" or "Resume" when there is something to run
    rerun_confirms: dict[str, str]  # what to ask first, by each done Stage that may run again
    resolve_label: str | None  # Resolving References, once Ingested
    shown_stages: tuple[str, ...]
    errors: list[str]


def _confirm(stage: str) -> str:
    stages = INGESTING_STAGES[INGESTING_STAGES.index(stage) :]
    named = stages[0] if len(stages) == 1 else f"{', '.join(stages[:-1])} and {stages[-1]}"
    question = f"Re-run {named}?"
    if set(TEXT_STAGES).intersection(stages):
        return f"{question} A Conversation about this Work starts afresh."
    return question


def row(work: LibraryWork, message: str | None = None) -> Row:
    """What the row of this Library Item shows and offers."""
    done = [work.stages.get(s) == "done" for s in INGESTING_STAGES]
    states = work.stages.values()
    idle = work.run is None and "running" not in states
    ingested = all(done)
    started = any(work.stages.get(s) != "pending" for s in INGESTING_STAGES)
    resolved = all(work.stages.get(s) == "done" for s in RESOLVING_STAGES)
    return Row(
        work=work,
        message=message,
        polling=not idle,
        start_label=None
        if not idle or not work.has_pdf or ingested
        else ("Resume" if started else "Ingest"),
        rerun_confirms={
            stage: _confirm(stage)
            for stage, is_done in zip(INGESTING_STAGES, done, strict=True)
            if is_done and idle and work.has_pdf
        },
        resolve_label=None
        if not ingested or not idle
        else ("Resolve again" if resolved else "Resolve References"),
        shown_stages=INGESTING_STAGES + RESOLVING_STAGES if ingested else INGESTING_STAGES,
        errors=[
            f"{name}: {run.error}"
            for name, run in work.stage_statuses.items()
            if run.state == "failed" and run.error
        ],
    )


class LibraryList(BaseModel):
    """The rows found, and where more of them load from."""

    rows: list[Row]
    more_url: str | None


def library_list(page: LibraryPage, query: str, offset: int) -> LibraryList:
    """The rows of a page of the Library, for the search that found them."""
    following = offset + len(page.items)
    more = following < page.total and bool(page.items)
    return LibraryList(
        rows=[row(w) for w in page.items],
        more_url=f"/library?{urlencode({'q': query, 'offset': following})}" if more else None,
    )
