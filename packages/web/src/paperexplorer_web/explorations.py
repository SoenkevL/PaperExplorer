"""How a Seed's Exploration reads on its row and the Work page: its counts, and what is missing."""

from pydantic import BaseModel

from paperexplorer_api.schemas import Exploration


class ExplorationView(BaseModel):
    """An Exploration ready to render."""

    exploration: Exploration
    counts: str  # "17 filed, 2 awaiting your choice, 169 Unchecked"
    missing: list[str]  # what keeps it partial, each beside *Resume Exploration*
    resume: bool  # partial, and no Stage Run holds the Seed
    polling: bool  # a Stage Run holds the Seed, its Check included


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def exploration_view(exploration: Exploration, *, idle: bool) -> ExplorationView:
    """What the Exploration shows; `idle` when no Stage Run holds its Seed."""
    e = exploration
    counts = [f"{e.filed} filed"]
    if e.removed_by_reader:
        counts.append(f"{e.removed_by_reader} taken out by you")
    if e.awaiting_choice:
        counts.append(f"{e.awaiting_choice} awaiting your choice")
    if e.unchecked:
        counts.append(f"{e.unchecked} Unchecked")
    missing = [f"{stage} not done" for stage in e.stages_not_done]
    if not e.created:
        missing.append("its Collection is not in Zotero yet")
    if e.failed:
        missing.append(_plural(len(e.failed), "Filing failed", "Filings failed"))
    if e.not_filed:
        missing.append(_plural(e.not_filed, "Source not filed yet", "Sources not filed yet"))
    running = e.run is not None or not idle
    return ExplorationView(
        exploration=e,
        counts=", ".join(counts),
        missing=missing,
        resume=bool(missing) and not running,
        polling=running,
    )
