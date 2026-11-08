"""Explorations in Zotero: each Seed's Exploration Collection and the Filing of its Sources.

Writes go only to the PaperExplorer Collection's branch (ADR 0014); `reconcile` is not a Stage
and commits each Filing as its write lands (docs/design-explorations.md).
"""

import logging

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.catalog.new_items import new_item
from paperexplorer.domain import (
    Collection,
    Exploration,
    Filing,
    FilingState,
    ResolutionState,
    Work,
    WorkId,
)
from paperexplorer.library import (
    PAPEREXPLORER,
    ItemNotFoundError,
    Library,
    LibraryUnavailableError,
    LibraryWriteError,
    OutsidePaperExplorerError,
    new_key,
    sync,
)
from paperexplorer.store import Collections, Explorations, ReadersChoices, Resolutions, Works

__all__ = [
    "AmbiguousPaperExplorerError",
    "ExplorationState",
    "NotASeedError",
    "ReconcileResult",
    "exploration_name",
    "new_item",
    "reconcile",
    "start",
    "state",
]

logger = logging.getLogger(__name__)

Conn = psycopg.Connection[TupleRow]

_TITLE = 60  # the most of a Seed's title an Exploration Collection's name holds
# A write refused, unreachable, or outside the PaperExplorer Collection (the reader renamed or
# moved it): recorded, and tried again by the next run.
_WRITE_ERRORS = (
    LibraryWriteError,
    LibraryUnavailableError,
    ItemNotFoundError,
    OutsidePaperExplorerError,
)


class NotASeedError(ValueError):
    """The Work has no Library Item with a PDF, so it cannot be a Seed."""


class AmbiguousPaperExplorerError(Exception):
    """Two top-level Collections are named PaperExplorer, and PaperExplorer will not choose."""


class ReconcileResult(BaseModel):
    """What a reconcile wrote: Filings made (items created among them), taken back, failed."""

    filed: int = 0
    created: int = 0
    unfiled: int = 0
    failed: int = 0


class ExplorationState(BaseModel):
    """Where an Exploration stands: its Filings, and the Sources still waiting on something."""

    exploration: Exploration
    filed: int
    failed: list[Filing]
    removed_by_reader: int
    not_filed: int  # firm Sources waiting to be filed: a reconcile has not reached them yet
    awaiting_choice: int  # Sources matched only at low confidence
    unchecked: int  # References no Registry Check has looked up


def exploration_name(work: Work) -> str:
    """`<first author surname> <year>`, an en dash and the title, cut at 60 characters on a word."""
    surname = work.creators[0].split(",", 1)[0].strip() if work.creators else ""
    title = (work.title or "").strip()
    if len(title) > _TITLE:
        title = title[:_TITLE].rsplit(" ", 1)[0].rstrip(" ,;:") + "\N{HORIZONTAL ELLIPSIS}"
    lead = " ".join(p for p in (surname, str(work.year) if work.year else "") if p)
    return f"{lead} \N{EN DASH} {title}" if lead else title or "Untitled"


def start(conn: Conn, library: Library, seed: WorkId) -> Exploration:
    """Start the Seed's Exploration, or continue the one it has, and file what is firm."""
    with conn.transaction():
        sync(conn, library)
    work = Works(conn).get(seed)
    if work is None or work.zotero_key is None or work.pdf is None:
        raise NotASeedError(f"Work {seed} has no Library Item with a PDF; a Seed needs its text")
    explorations = Explorations(conn)
    with explorations.turn(
        None
    ):  # starts take turns, so only one makes the PaperExplorer Collection
        if explorations.of_seed(seed) is None:
            _root(conn, library)
            with conn.transaction():
                explorations.start(seed, new_key(), exploration_name(work))
            logger.info("Exploration of %s started", work.zotero_key)
    reconcile(conn, library, seed)
    started = explorations.of_seed(seed)
    assert started is not None
    return started


def reconcile(conn: Conn, library: Library, seed: WorkId) -> ReconcileResult | None:
    """File the Seed's firm Sources and take back what PaperExplorer filed that is not firm.

    `None` when the Seed has no Exploration under way. Each write commits as it lands; one that
    fails is recorded with its reason and tried again by the next run.
    """
    with Explorations(conn).turn(seed):
        with conn.transaction():
            sync(conn, library)
        _untag_ended(conn, library)
        explorations = Explorations(conn)
        exploration = explorations.of_seed(seed)
        root = explorations.root()
        if exploration is None or root is None:
            return None
        result = ReconcileResult()
        if not exploration.created:
            try:
                _create_collection(conn, library, exploration, root.key)
            except _WRITE_ERRORS as e:
                logger.warning("Exploration Collection %s not created: %s", exploration.name, e)
                return ReconcileResult(failed=1)
        wanted = _wanted(conn, seed)
        chosen = ReadersChoices(conn).chosen_at(seed)
        filings = {f.work: f for f in explorations.filings(exploration.id)}
        for work in wanted:
            filing = filings.get(work)
            if filing is not None and filing.state is FilingState.FILED:
                continue
            if (
                filing is not None
                and filing.state is FilingState.REMOVED_BY_READER
                and ((picked := chosen.get(work)) is None or picked < filing.updated_at)
            ):
                continue  # the reader took it out and has not chosen it since it was filed
            _file(conn, library, exploration, root.key, work, filing, result)
        for work, filing in filings.items():
            if work not in wanted:
                _unfile(conn, library, exploration, root.key, filing, result)
        if not exploration.seed_tagged:
            _tag_seed(conn, library, exploration)
        logger.info(
            "Reconciled the Exploration of Work %d: %d filed (%d created), %d unfiled, %d failed",
            seed, result.filed, result.created, result.unfiled, result.failed,
        )  # fmt: skip
        return result


def state(conn: Conn, seed: WorkId) -> ExplorationState | None:
    """Where the Seed's Exploration stands, or `None` if it has none under way."""
    explorations = Explorations(conn)
    exploration = explorations.of_seed(seed)
    if exploration is None:
        return None
    filings = {f.work: f for f in explorations.filings(exploration.id)}
    resolutions = Resolutions(conn).for_work(seed)
    return ExplorationState(
        exploration=exploration,
        filed=sum(f.state is FilingState.FILED for f in filings.values()),
        failed=[f for f in filings.values() if f.state is FilingState.FAILED],
        removed_by_reader=sum(f.state is FilingState.REMOVED_BY_READER for f in filings.values()),
        not_filed=sum(w not in filings for w in _wanted(conn, seed)),
        awaiting_choice=sum(r.awaiting_choice for r in resolutions),
        unchecked=sum(r.state is ResolutionState.UNCHECKED for r in resolutions),
    )


def _root(conn: Conn, library: Library) -> str:
    """The PaperExplorer Collection's key: the one recorded, else adopted, else created."""
    explorations, collections = Explorations(conn), Collections(conn)
    held = {c.key: c for c in collections.list()}
    root = explorations.root()
    if root is not None and root.created and root.key in held:
        return root.key
    if root is None or root.created:  # none yet, or the reader deleted it
        named = [c for c in held.values() if c.parent_key is None and c.name == PAPEREXPLORER]
        if len(named) > 1:
            raise AmbiguousPaperExplorerError(
                f"{len(named)} top-level Collections are named {PAPEREXPLORER};"
                " rename all but one in Zotero"
            )
        if named:
            with conn.transaction():
                explorations.set_root(named[0].key, created=True)
            logger.info("Adopted the %s Collection %s", PAPEREXPLORER, named[0].key)
            return named[0].key
        with conn.transaction():
            explorations.set_root(new_key(), created=False)
        root = explorations.root()
        assert root is not None
    library.create_paperexplorer_collection(root.key)
    with conn.transaction():
        collections.record(Collection(key=root.key, name=PAPEREXPLORER))
        explorations.set_root(root.key, created=True)
    logger.info("Created the %s Collection %s", PAPEREXPLORER, root.key)
    return root.key


def _create_collection(conn: Conn, library: Library, exploration: Exploration, root: str) -> None:
    library.create_collection(exploration.collection_key, exploration.name, within=root)
    with conn.transaction():
        # Recorded now, so the next sync does not take it for one restored from the trash.
        Collections(conn).record(
            Collection(key=exploration.collection_key, name=exploration.name, parent_key=root)
        )
        Explorations(conn).mark_created(exploration.id)


def _wanted(conn: Conn, seed: WorkId) -> list[WorkId]:
    """The Seed and the Works its firm Resolutions name, each once, in printed order."""
    wanted = [seed]
    for r in Resolutions(conn).for_work(seed):
        if r.firm and r.work is not None and r.work not in wanted:
            wanted.append(r.work)
    return wanted


def _file(
    conn: Conn,
    library: Library,
    exploration: Exploration,
    root: str,
    work_id: WorkId,
    filing: Filing | None,
    result: ReconcileResult,
) -> None:
    """File one Work, creating its Library Item first if the Library holds none."""
    works, explorations = Works(conn), Explorations(conn)
    work = works.get(work_id)
    if work is None:
        return
    if (
        work.zotero_key is None
        and filing is not None
        and filing.state in (FilingState.PENDING, FilingState.FAILED)
        and (held := works.find(filing.item_key)) is not None
    ):
        # Created, but its answer was lost: the sync has since made the item its own Work, which
        # the References and choices now name.
        with conn.transaction():
            Resolutions(conn).repoint(work_id, held.id)
            ReadersChoices(conn).repoint(work_id, held.id)
            explorations.forget_filing(exploration.id, work_id)
        work_id, work, filing = held.id, held, None
    key, create = _item_of(conn, work, filing)
    try:
        if create:
            with conn.transaction():  # the key is kept before the create, so a retry reuses it
                explorations.record_filing(exploration.id, work_id, key, FilingState.PENDING)
            library.create_item(
                key, new_item(conn, work), collection=exploration.collection_key, within=root
            )
            with conn.transaction():
                works.assign_library_item(work_id, key)
            result.created += 1
        else:
            library.file(key, exploration.collection_key, within=root)
    except _WRITE_ERRORS as e:
        with conn.transaction():
            explorations.record_filing(exploration.id, work_id, key, FilingState.FAILED, str(e))
        logger.warning("Filing %s in %s failed: %s", key, exploration.name, e)
        result.failed += 1
        return
    with conn.transaction():
        explorations.record_filing(exploration.id, work_id, key, FilingState.FILED)
    result.filed += 1


def _item_of(conn: Conn, work: Work, filing: Filing | None) -> tuple[str, bool]:
    """The item to file for a Work, and whether it must be created first.

    A Work found by a Registry Check may since have a Library Item holding its DOI: that one is
    filed. A key kept by an earlier, unconfirmed create is used again.
    """
    if work.zotero_key is not None:
        return work.zotero_key, False
    works = Works(conn)
    if work.doi and (standing := works.standing_item(work.doi)) is not None:
        held = works.get(standing)
        if held is not None and held.zotero_key is not None:
            return held.zotero_key, False
    if filing is not None and filing.state in (FilingState.PENDING, FilingState.FAILED):
        return filing.item_key, True
    return new_key(), True


def _unfile(
    conn: Conn,
    library: Library,
    exploration: Exploration,
    root: str,
    filing: Filing,
    result: ReconcileResult,
) -> None:
    """Take back a Filing PaperExplorer made; one the reader removed is only forgotten."""
    if filing.state is not FilingState.REMOVED_BY_READER:
        try:
            library.unfile(filing.item_key, exploration.collection_key, within=root)
        except ItemNotFoundError:
            pass  # gone from the Library: nothing is filed
        except _WRITE_ERRORS as e:
            logger.warning("Unfiling %s from %s failed: %s", filing.item_key, exploration.name, e)
            result.failed += 1
            return
        result.unfiled += 1
    with conn.transaction():
        Explorations(conn).forget_filing(exploration.id, filing.work)


def _tag_seed(conn: Conn, library: Library, exploration: Exploration) -> None:
    work = Works(conn).get(exploration.seed)
    if work is None or work.zotero_key is None:
        return
    try:
        library.tag_seed(work.zotero_key)
    except _WRITE_ERRORS as e:
        logger.warning("Tagging the Seed %s failed: %s", work.zotero_key, e)
        return
    with conn.transaction():
        Explorations(conn).mark_seed_tagged(exploration.id, tagged=True)


def _untag_ended(conn: Conn, library: Library) -> None:
    """Take `pe:seed` off the Seeds of ended Explorations that are no other's Seed."""
    explorations = Explorations(conn)
    for ended in explorations.untag_pending():
        work = Works(conn).get(ended.seed)
        if work is not None and work.zotero_key is not None:
            try:
                library.untag_seed(work.zotero_key)
            except ItemNotFoundError:
                pass
            except _WRITE_ERRORS as e:
                logger.warning("Untagging the Seed %s failed: %s", work.zotero_key, e)
                continue
        with conn.transaction():
            explorations.mark_seed_tagged(ended.id, tagged=False)
