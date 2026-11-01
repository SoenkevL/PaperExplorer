"""The `verify_dois` Stage: each printed DOI asked once, and what resolving makes of it."""

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import Level, LibraryItem, ResolutionState, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.pipeline import StageContext, run_stage
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import Resolutions, StageState, StageStatuses, Works, WorkTexts
from paperexplorer.tracing import Tracing

Conn = psycopg.Connection[TupleRow]


NEURON = LibraryItem(
    key="NEUR2020",
    title="Dendritic integration in cortical neurons",
    creators=["Larkum, Matthew"],
    year=2020,
    doi="10.1016/j.neuron.2020.01.026",
)
# GROBID cut the DOI short at a line end: doi.org has no record of it.
CUT_SHORT = "10.1016/j.neuron.2020.01.02"


@pytest.fixture
def registry() -> InMemoryDoiRegistry:
    return InMemoryDoiRegistry(registered={"10.5555/attention", "10.5555/resnet", NEURON.doi or ""})


@pytest.fixture
def ctx(store_db: Conn, registry: InMemoryDoiRegistry) -> StageContext:
    return StageContext(
        conn=store_db,
        settings=Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        library=InMemoryLibrary([NEURON], {}),
        embedder=FakeEmbedder(),
        tracing=Tracing.off(),
        doi_registry=registry,
    )


def _ref(key: str, **fields: object) -> Reference:
    return Reference.model_validate({"id": key, "text": f"Printed {key}", "page": 1} | fields)


def _seed(conn: Conn, refs: list[Reference]) -> WorkId:
    seed = Works(conn).ensure("SEED1111")
    WorkTexts(conn).replace(
        seed, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=refs)
    )
    return seed


def test_each_printed_doi_is_counted_registered_or_unregistered(ctx: StageContext) -> None:
    seed = _seed(
        ctx.conn,
        [
            _ref("b0", doi="https://doi.org/10.5555/ATTENTION"),
            _ref("b1", doi="10.5555/resnet"),
            _ref("b2", doi="10.5555/cut.shor"),
            _ref("b3", title="No DOI printed"),
        ],
    )

    counts = run_stage(ctx, "verify_dois", seed).counts

    assert counts == {"dois": 3, "registered": 2, "unregistered": 1, "unverified": 0}


def test_a_doi_is_asked_once_and_an_unregistered_one_again_after_30_days(
    ctx: StageContext, registry: InMemoryDoiRegistry
) -> None:
    seed = _seed(ctx.conn, [_ref("b0", doi="10.5555/attention"), _ref("b1", doi="10.5555/cut")])
    run_stage(ctx, "verify_dois", seed)

    run_stage(ctx, "verify_dois", seed)
    assert registry.asked == ["10.5555/attention", "10.5555/cut"]

    ctx.conn.execute("UPDATE registry_responses SET fetched_at = now() - interval '31 days'")
    counts = run_stage(ctx, "verify_dois", seed).counts
    assert registry.asked[2:] == ["10.5555/cut"]
    assert counts["unregistered"] == 1


def test_offline_the_stage_is_done_with_its_dois_unverified_and_a_warning(
    ctx: StageContext, registry: InMemoryDoiRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    registry.offline = True
    seed = _seed(ctx.conn, [_ref("b0", doi="10.5555/attention"), _ref("b1", doi="10.5555/cut")])

    counts = run_stage(ctx, "verify_dois", seed).counts

    assert counts == {"dois": 2, "registered": 0, "unregistered": 0, "unverified": 2}
    assert StageStatuses(ctx.conn).for_work(seed)["verify_dois"].state is StageState.DONE
    warning = "2 DOIs unverified, doi.org unreachable or did not answer; rerun verify_dois"
    assert warning in caplog.text


def test_verifying_again_makes_resolve_pending(
    ctx: StageContext, registry: InMemoryDoiRegistry
) -> None:
    registry.offline = True
    seed = _seed(ctx.conn, [_ref("b0", doi="10.5555/attention")])
    run_stage(ctx, "verify_dois", seed)
    run_stage(ctx, "resolve", seed)

    registry.offline = False
    counts = run_stage(ctx, "verify_dois", seed).counts

    assert counts["registered"] == 1
    assert "resolve" not in StageStatuses(ctx.conn).for_work(seed)


def _neuron_ref() -> Reference:
    return _ref("b0", title=NEURON.title, surnames=["Larkum"], year="2020", doi=CUT_SHORT)


def test_an_unregistered_doi_is_resolved_as_if_none_were_printed(ctx: StageContext) -> None:
    seed = _seed(ctx.conn, [_neuron_ref()])
    run_stage(ctx, "verify_dois", seed)

    run_stage(ctx, "resolve", seed)

    [found] = Resolutions(ctx.conn).for_work(seed)
    neuron = Works(ctx.conn).find(NEURON.key)
    assert neuron is not None
    assert (found.state, found.work, found.level) == (
        ResolutionState.RESOLVED, neuron.id, Level.HIGH,
    )  # fmt: skip
    assert f"unregistered DOI {CUT_SHORT}" in found.evidence


def test_a_registered_doi_in_no_library_item_stays_unchecked(ctx: StageContext) -> None:
    seed = _seed(ctx.conn, [_ref("b0", title="Attention", doi="10.5555/attention")])
    run_stage(ctx, "verify_dois", seed)

    run_stage(ctx, "resolve", seed)

    [found] = Resolutions(ctx.conn).for_work(seed)
    assert found.state is ResolutionState.UNCHECKED
    assert found.evidence == []


def test_an_unverified_doi_still_counts_as_printed(
    ctx: StageContext, registry: InMemoryDoiRegistry
) -> None:
    registry.offline = True
    seed = _seed(ctx.conn, [_neuron_ref()])
    run_stage(ctx, "verify_dois", seed)

    run_stage(ctx, "resolve", seed)

    [found] = Resolutions(ctx.conn).for_work(seed)
    assert found.state is ResolutionState.UNCHECKED
