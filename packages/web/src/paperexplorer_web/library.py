"""How a Library Item's row reads: what it shows and offers for where its Stages stand."""

from urllib.parse import urlencode

from pydantic import BaseModel

from paperexplorer_api.schemas import (
    INGESTING_STAGES,
    RESOLVING_STAGES,
    TEXT_STAGES,
    LibraryPage,
    LibraryWork,
    StageState,
    rerun_stages,
)
from paperexplorer_web.browsing import Selection, Tree
from paperexplorer_web.explorations import ExplorationView, exploration_view


class Pip(BaseModel):
    """One Stage on a row's strip; a done Stage that may run again is the button re-running it."""

    stage: str
    state: StageState
    rerun_confirm: str | None  # what to ask first, when it may run again


class Filed(BaseModel):
    """A Collection a row's Work is filed in, as its "also in" line links it."""

    name: str
    path: str
    url: str


class Row(BaseModel):
    """A Library Item ready to render."""

    work: LibraryWork
    message: str | None = None
    polling: bool
    start_label: str | None  # "Ingest" or "Resume" when there is something to run
    resolve_label: str | None  # Resolving References, once parsed
    explore: bool  # *Start Exploration*: a Work with a PDF that is the Seed of none
    exploration: ExplorationView | None  # the Exploration it is the Seed of
    pips: list[Pip]
    stages_label: str  # every Stage and its state, for the strip's aria-label
    errors: list[str]
    filed_label: str  # "also in" beside a selected Collection, else "in"
    filed: list[Filed]  # its Collections but the one selected
    browsing: bool  # in the Collections view, where a Collection's link swaps the Works side
    tint: bool  # a match in the selected branch, while searching it


def _confirm(stage: str) -> str:
    stages = rerun_stages(stage)
    named = stages[0] if len(stages) == 1 else f"{', '.join(stages[:-1])} and {stages[-1]}"
    question = f"Re-run {named}?"
    if set(TEXT_STAGES).intersection(stages):
        return f"{question} A Conversation about this Work starts afresh."
    return question


def row(
    work: LibraryWork,
    message: str | None = None,
    *,
    pane: Tree | None = None,
    selected: str | None = None,
    browsing: bool = False,
    tint: bool = False,
) -> Row:
    """What the row of this Library Item shows and offers.

    `pane` names its Collections (none without it), and `selected`, the Collection browsed, is
    left out of them.
    """
    states = work.stages.values()
    idle = work.run is None and "running" not in states
    ingested = all(work.stages.get(s) == "done" for s in INGESTING_STAGES)
    started = any(work.stages.get(s) != "pending" for s in INGESTING_STAGES)
    parsed = work.stages.get("parse") == "done"
    resolved = all(work.stages.get(s) == "done" for s in RESOLVING_STAGES)
    may_rerun = idle and work.has_pdf
    pips = [
        Pip(
            stage=stage,
            state=work.stages.get(stage, "pending"),
            rerun_confirm=_confirm(stage)
            if may_rerun and stage in INGESTING_STAGES and work.stages.get(stage) == "done"
            else None,
        )
        for stage in INGESTING_STAGES + RESOLVING_STAGES
    ]
    filed = [
        Filed(name=node.name, path=node.path, url=node.url)
        for key in work.collections
        if key != selected and pane is not None and (node := pane.by_key.get(key)) is not None
    ]
    explored = exploration_view(work.exploration, idle=idle) if work.exploration else None
    return Row(
        work=work,
        message=message,
        polling=not idle,
        # *Resume Exploration* runs every Stage not done, so it stands in for *Resume*.
        start_label=None
        if not idle or not work.has_pdf or ingested or (explored is not None and explored.resume)
        else ("Resume" if started else "Ingest"),
        resolve_label=None
        if not parsed or not idle
        else ("Resolve again" if resolved else "Resolve References"),
        explore=idle and work.has_pdf and work.exploration is None,
        exploration=explored,
        pips=pips,
        stages_label="Stages: " + ", ".join(f"{p.stage} {p.state}" for p in pips),
        errors=[
            f"{name}: {run.error}"
            for name, run in work.stage_statuses.items()
            if run.state == "failed" and run.error
        ],
        filed_label="also in" if selected else "in",
        filed=filed,
        browsing=browsing,
        tint=tint,
    )


class LibraryList(BaseModel):
    """The rows found, where more of them load from, and where the branch's matches end."""

    rows: list[Row]
    more_url: str | None
    divider_at: int | None = None  # the row on this page *Elsewhere in the Library* goes before
    elsewhere: int = 0  # how many matches lie outside the branch


def library_list(
    page: LibraryPage,
    query: str,
    offset: int,
    *,
    selection: Selection | None = None,
    pane: Tree | None = None,
) -> LibraryList:
    """The rows of a page of the Library, for the search that found them.

    With a `selection`, the Collections view's: a search lists the branch's matches first, on a
    tint, and the rest after *Elsewhere in the Library*, on whichever page that falls.
    """
    following = offset + len(page.items)
    more = following < page.total and bool(page.items)
    searching_branch = selection is not None and selection.is_branch and bool(query.split())
    divider = page.branch_total - offset
    params: dict[str, str | int] = {"q": query, "offset": following}
    if selection is not None:
        if selection.unfiled:
            params["unfiled"] = "1"
        else:  # with no key, the whole Library, still in the Collections view
            params["collection"] = selection.collection or ""
            if selection.deep:
                params["deep"] = "1"
    selected = selection.collection if selection else None
    return LibraryList(
        rows=[
            row(
                w,
                pane=pane,
                selected=selected,
                browsing=selection is not None,
                tint=searching_branch and w.in_branch,
            )
            for w in page.items
        ],
        more_url=f"/library?{urlencode(params)}" if more else None,
        divider_at=divider
        if searching_branch and page.branch_total < page.total and 0 <= divider < len(page.items)
        else None,
        elsewhere=page.total - page.branch_total,
    )
