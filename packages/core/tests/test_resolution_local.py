"""The `resolve` Stage: every Reference of a Work matched against the Library, synced first."""

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import (
    Attachment,
    Layer,
    Level,
    LibraryItem,
    Resolution,
    ResolutionState,
    WorkId,
)
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.pipeline import StageContext, StageError, run_stage
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import Resolutions, StageStatuses, Works, WorkTexts
from paperexplorer.tracing import Tracing

Conn = psycopg.Connection[TupleRow]

SEED = LibraryItem(key="SEED1111", title="The Seed", creators=["Seed, Sam"], year=2024)
ATTENTION = LibraryItem(
    key="ATTN2017",
    title="Attention Is All You Need",
    creators=["Vaswani, Ashish"],
    year=2017,
    doi="10.5555/attention",
)
RESNET = LibraryItem(
    key="RESN2016",
    title="Deep residual learning for image recognition",
    creators=["He, Kaiming"],
    year=2016,
    doi="10.5555/resnet",
)


@pytest.fixture
def library() -> InMemoryLibrary:
    return InMemoryLibrary([SEED, ATTENTION, RESNET], {})


@pytest.fixture
def ctx(store_db: Conn, library: InMemoryLibrary) -> StageContext:
    return StageContext(
        conn=store_db,
        settings=Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        library=library,
        embedder=FakeEmbedder(),
        tracing=Tracing.off(),
        doi_registry=InMemoryDoiRegistry(offline=True),
    )


def _ref(key: str, **fields: object) -> Reference:
    return Reference.model_validate({"id": key, "text": f"Printed {key}", "page": 1} | fields)


def _seed(conn: Conn, refs: list[Reference]) -> WorkId:
    seed = Works(conn).ensure(SEED.key)
    WorkTexts(conn).replace(
        seed, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=refs)
    )
    return seed


def _work(conn: Conn, key: str) -> WorkId:
    found = Works(conn).find(key)
    assert found is not None
    return found.id


def test_references_resolve_against_the_library_by_doi_and_by_title(ctx: StageContext) -> None:
    seed = _seed(
        ctx.conn,
        [
            _ref("b0", doi="https://doi.org/10.5555/RESNET", title=RESNET.title),
            _ref("b1", title="Attention is all you need", surnames=["Vaswani"], year="2017"),
            _ref("b2", title="A paper nobody here holds", surnames=["Nobody"], year="1999"),
        ],
    )

    result = run_stage(ctx, "resolve", seed)

    resnet, attention = _work(ctx.conn, RESNET.key), _work(ctx.conn, ATTENTION.key)
    assert Resolutions(ctx.conn).for_work(seed) == [
        Resolution(
            reference="b0", state=ResolutionState.RESOLVED, work=resnet, level=Level.CERTAIN,
            confidence=1.0, evidence=["doi"], layer=Layer.LIBRARY,
        ),
        Resolution(
            reference="b1", state=ResolutionState.RESOLVED, work=attention, level=Level.HIGH,
            confidence=1.0, evidence=["title", "surname", "year"], layer=Layer.LIBRARY,
        ),
        Resolution(reference="b2", state=ResolutionState.UNCHECKED),
    ]  # fmt: skip
    assert result.counts == {
        "references": 3, "certain": 1, "high": 1, "low": 0, "unchecked": 1, "doi_conflicts": 0,
    }  # fmt: skip


def test_a_doi_conflict_lets_the_title_win_at_low_and_keeps_the_dois_work(
    ctx: StageContext,
) -> None:
    # A neighbour's DOI: it names RESNET, the title names ATTENTION.
    seed = _seed(
        ctx.conn,
        [_ref("b0", doi="10.5555/resnet", title=ATTENTION.title, surnames=["Vaswani"])],
    )

    result = run_stage(ctx, "resolve", seed)

    [resolution] = Resolutions(ctx.conn).for_work(seed)
    assert (resolution.work, resolution.level, resolution.doi_conflict) == (
        _work(ctx.conn, ATTENTION.key),
        Level.LOW,
        _work(ctx.conn, RESNET.key),
    )
    assert "doi_conflict" in resolution.evidence
    assert result.counts["doi_conflicts"] == 1


def test_a_doi_two_library_items_hold_resolves_to_the_one_that_stands(
    ctx: StageContext, library: InMemoryLibrary
) -> None:
    pdf = Attachment(key="ATTPDF11", filename="a.pdf")
    copy = ATTENTION.model_copy(update={"key": "ATTNCOPY", "pdf": pdf})
    library.put(ATTENTION.model_copy(update={"added": datetime(2020, 1, 1, tzinfo=UTC)}))
    library.put(copy.model_copy(update={"added": datetime(2025, 1, 1, tzinfo=UTC)}))
    seed = _seed(
        ctx.conn,
        [
            _ref("b0", doi="10.5555/attention"),
            _ref("b1", title=ATTENTION.title, surnames=["Vaswani"], year="2017"),
        ],
    )

    run_stage(ctx, "resolve", seed)

    stands = _work(ctx.conn, "ATTNCOPY")  # the one with a PDF
    assert [r.work for r in Resolutions(ctx.conn).for_work(seed)] == [stands, stands]


def test_the_library_is_synced_before_resolving(
    ctx: StageContext, library: InMemoryLibrary
) -> None:
    seed = _seed(ctx.conn, [_ref("b0", doi="10.5555/new")])
    run_stage(ctx, "resolve", seed)
    library.put(LibraryItem(key="NEWW2026", title="Added later", doi="10.5555/new"))

    run_stage(ctx, "resolve", seed)

    [resolution] = Resolutions(ctx.conn).for_work(seed)
    assert resolution.work == _work(ctx.conn, "NEWW2026")


def test_a_work_cites_no_reference_to_itself(ctx: StageContext) -> None:
    seed = _seed(ctx.conn, [_ref("b0", title=SEED.title, surnames=["Seed"], year="2024")])

    run_stage(ctx, "resolve", seed)

    assert Resolutions(ctx.conn).for_work(seed)[0].state is ResolutionState.UNCHECKED


def test_an_unparsed_work_cannot_be_resolved(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure(SEED.key)

    with pytest.raises(StageError, match="run parse first"):
        run_stage(ctx, "resolve", work)


def test_a_work_whose_library_item_went_no_longer_matches(
    ctx: StageContext, library: InMemoryLibrary
) -> None:
    seed = _seed(
        ctx.conn,
        [_ref("b0", doi="10.5555/resnet"), _ref("b1", title=RESNET.title, surnames=["He"])],
    )
    run_stage(ctx, "resolve", seed)
    StageStatuses(ctx.conn).start(_work(ctx.conn, RESNET.key), "fetch")  # kept: a Stage ran
    library.remove(RESNET.key)

    run_stage(ctx, "resolve", seed)

    assert [r.state for r in Resolutions(ctx.conn).for_work(seed)] == [
        ResolutionState.UNCHECKED,
        ResolutionState.UNCHECKED,
    ]
