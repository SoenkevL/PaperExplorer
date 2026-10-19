from collections.abc import Callable

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.citations import CitationGraph, CitationLink, Evidence, extract
from paperexplorer.domain import CitationId, WorkId
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import CitationGraphs, Works, WorkTexts

Conn = psycopg.Connection[TupleRow]
ParseFixture = Callable[[str], ParsedWork]


def _stored(conn: Conn, parsed: ParsedWork, key: str = "villiger") -> tuple[WorkId, CitationGraph]:
    work = Works(conn).ensure(key)
    WorkTexts(conn).replace(work, parsed)
    graph = extract(parsed)
    CitationGraphs(conn).replace(work, graph)
    return work, graph


@pytest.fixture
def villiger(parse_fixture: ParseFixture) -> ParsedWork:
    return parse_fixture("villiger2024-tandf")


def test_a_works_citations_come_back_in_order_with_their_links(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, graph = _stored(store_db, villiger)

    stored = CitationGraphs(store_db).for_work(work)

    assert [s.citation for s in stored] == graph.citations
    assert len({s.id for s in stored}) == len(graph.citations)


def test_storing_citations_again_replaces_them(store_db: Conn, villiger: ParsedWork) -> None:
    work, graph = _stored(store_db, villiger)

    CitationGraphs(store_db).replace(work, graph)

    assert len(CitationGraphs(store_db).for_work(work)) == len(graph.citations)


def test_a_linked_citation_comes_with_the_reference_it_points_at(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, graph = _stored(store_db, villiger)
    stored = next(s for s in CitationGraphs(store_db).for_work(work) if s.citation.link)
    assert stored.citation.link is not None

    detail = CitationGraphs(store_db).get(stored.id)

    assert detail is not None
    assert detail.work == work
    assert detail.citation == stored.citation
    expected = next(r for r in graph.references if r.id == stored.citation.link.reference_id)
    assert detail.reference == expected


def test_an_unlinked_citation_has_no_reference(store_db: Conn, villiger: ParsedWork) -> None:
    work = Works(store_db).ensure("villiger")
    WorkTexts(store_db).replace(work, villiger)
    graph = extract(villiger)
    graph.citations[0].link = None
    CitationGraphs(store_db).replace(work, graph)
    first = CitationGraphs(store_db).for_work(work)[0]

    detail = CitationGraphs(store_db).get(first.id)

    assert detail is not None
    assert detail.citation.link is None
    assert detail.reference is None


def test_an_unknown_citation_is_none(store_db: Conn) -> None:
    assert CitationGraphs(store_db).get(CitationId(-1)) is None


def test_parsing_again_drops_the_citations_of_the_old_text(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)

    WorkTexts(store_db).replace(work, villiger)

    assert CitationGraphs(store_db).for_work(work) == []


def test_a_link_to_a_reference_the_work_does_not_have_is_refused(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work = Works(store_db).ensure("villiger")
    WorkTexts(store_db).replace(work, villiger)
    graph = extract(villiger)
    graph.citations[0].link = CitationLink(
        reference_id="not-in-this-work", confidence=1.0, evidence=[Evidence.PDF_LINK]
    )

    with pytest.raises(ValueError, match="not-in-this-work"):
        CitationGraphs(store_db).replace(work, graph)


def test_a_work_without_citations_stores_none(store_db: Conn, villiger: ParsedWork) -> None:
    work = Works(store_db).ensure("villiger")
    WorkTexts(store_db).replace(work, villiger)

    CitationGraphs(store_db).replace(work, CitationGraph(references=[], citations=[]))

    assert CitationGraphs(store_db).for_work(work) == []
