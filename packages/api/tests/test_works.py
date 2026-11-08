from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.citations import Citation, CitationGraph, CitationLink, Evidence
from paperexplorer.domain import Layer, Level, LibraryItem, Resolution, ResolutionState, WorkId
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Reference, Span, TextOrigin
from paperexplorer.store import CitationGraphs, Resolutions, StageStatuses, Works, WorkTexts

from .conftest import Stored

Conn = psycopg.Connection[TupleRow]


def test_works_are_listed_with_their_metadata_and_stages(api: TestClient, villiger: Stored) -> None:
    response = api.get("/works")

    assert response.status_code == 200
    [work] = response.json()
    assert work["id"] == villiger.work
    assert work["zotero_key"] == "VILL2024"
    assert work["title"].startswith("Personal psychedelic experience")
    assert work["creators"] == ["Villiger, Daniel"]
    assert work["year"] == 2024
    assert work["text_origin"] == "printed"
    assert work["stages"] == {
        "fetch": "done", "parse": "done", "chunk": "done", "embed": "done",
        "extract_citations": "done", "verify_dois": "pending", "resolve": "pending",
    }  # fmt: skip


def test_a_stage_that_never_ran_is_pending(api: TestClient, store_db: Conn) -> None:
    work_id = Works(store_db).ensure("NEW23456")
    StageStatuses(store_db).start(work_id, "fetch")

    [work] = api.get("/works").json()

    assert work["stages"] == {
        "fetch": "running", "parse": "pending", "chunk": "pending", "embed": "pending",
        "extract_citations": "pending", "verify_dois": "pending", "resolve": "pending",
    }  # fmt: skip
    assert work["text_origin"] is None


def test_a_library_only_work_is_not_listed(
    api: TestClient, villiger: Stored, store_db: Conn
) -> None:
    Works(store_db).ensure("LIBONLY1")  # synced from the Library, no Stage run

    assert [w["zotero_key"] for w in api.get("/works").json()] == ["VILL2024"]


def test_a_work_is_its_blocks_in_reading_order(api: TestClient, villiger: Stored) -> None:
    response = api.get(f"/works/{villiger.work}")

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert [b["text"] for b in blocks] == [b.text for b in villiger.parsed.blocks]
    first = blocks[0]
    assert first["position"] == 0
    assert first["kind"] == villiger.parsed.blocks[0].kind.value
    assert first["heading_path"] == villiger.parsed.blocks[0].heading_path
    assert first["page"] == villiger.parsed.blocks[0].page
    assert len(first["boxes"]) == len(villiger.parsed.blocks[0].boxes)


def test_citation_spans_are_offsets_into_their_blocks_text(
    api: TestClient, villiger: Stored
) -> None:
    blocks: list[dict[str, Any]] = api.get(f"/works/{villiger.work}").json()["blocks"]

    spans = [(b["text"], c) for b in blocks for c in b["citations"]]
    assert [text[c["start"] : c["end"]] for text, c in spans] == [
        c.text for c in villiger.graph.citations
    ]
    assert len({c["id"] for _, c in spans}) == 90
    levels = Counter(c["confidence_level"] for _, c in spans)
    assert levels == {"certain": 84, "high": 5, "low": 1}


def test_an_unknown_work_is_not_found(api: TestClient) -> None:
    assert api.get("/works/999999").status_code == 404


def test_a_work_not_parsed_yet_has_no_text_to_show(api: TestClient, store_db: Conn) -> None:
    work = Works(store_db).ensure("NEW23456")

    response = api.get(f"/works/{work}")

    assert response.status_code == 404
    assert "parse" in response.json()["detail"]


def test_ocr_text_is_marked_as_such(
    api: TestClient,
    store_work: Callable[[LibraryItem, ParsedWork], Stored],
    parse_fixture: Callable[[str], ParsedWork],
) -> None:
    scan = store_work(LibraryItem(key="SCAN2345"), parse_fixture("scan-villiger2024"))

    work = api.get(f"/works/{scan.work}").json()

    assert work["text_origin"] == "ocr"
    assert all(b["ocr_confidence"] is not None for b in work["blocks"])


def _cited_references(villiger: Stored) -> list[str]:
    """Reference keys in the order the Work first cites them."""
    keys = [c.link.reference_id for c in villiger.graph.citations if c.link]
    return list(dict.fromkeys(keys))


def _library_work(conn: Conn, key: str, title: str) -> WorkId:
    work = Works(conn).ensure(key)
    Works(conn).record_library_item(work, LibraryItem(key=key, title=title, year=2020))
    return work


def test_a_citation_whose_reference_has_a_library_match_says_so(
    api: TestClient, villiger: Stored, store_db: Conn
) -> None:
    first, second, third, fourth = _cited_references(villiger)[:4]
    matched = _library_work(store_db, "LIBM0001", "A Work the reader holds")
    later = _library_work(store_db, "LIBM0002", "Another Work the reader holds")
    chosen = _library_work(store_db, "LIBM0003", "A Work the reader picked")
    near = _library_work(store_db, "LIBM0004", "A near title")

    def resolved(ref: str, work: WorkId, level: Level, layer: Layer) -> Resolution:
        return Resolution(
            reference=ref, state=ResolutionState.RESOLVED, work=work, level=level, layer=layer
        )

    for r in (
        resolved(second, later, Level.CERTAIN, Layer.LIBRARY),
        resolved(first, matched, Level.HIGH, Layer.LIBRARY),
        resolved(third, chosen, Level.CERTAIN, Layer.READER),  # a Reader's Choice is not one
        resolved(fourth, near, Level.LOW, Layer.LIBRARY),  # nor is a low Library match
    ):
        Resolutions(store_db).save(villiger.work, r)

    text = api.get(f"/works/{villiger.work}").json()

    spans = {c["id"]: c for b in text["blocks"] for c in b["citations"]}
    by_reference = {
        r: [i for i, c in zip(spans, villiger.graph.citations, strict=True)
            if c.link and c.link.reference_id == r]
        for r in (first, second)
    }  # fmt: skip
    assert {i for i, c in spans.items() if c["library_match"]} == {
        *by_reference[first],
        *by_reference[second],
    }
    assert [(m["work"]["zotero_key"], m["work"]["title"], m["citations"])
            for m in text["library_matches"]] == [
        ("LIBM0001", "A Work the reader holds", by_reference[first]),
        ("LIBM0002", "Another Work the reader holds", by_reference[second]),
    ]  # fmt: skip


def test_a_library_match_whose_library_item_is_gone_is_not_shown(
    api: TestClient, villiger: Stored, store_db: Conn
) -> None:
    [first] = _cited_references(villiger)[:1]
    work = _library_work(store_db, "LIBM0001", "Deleted in Zotero since")
    Resolutions(store_db).save(
        villiger.work,
        Resolution(
            reference=first, state=ResolutionState.RESOLVED, work=work, level=Level.CERTAIN,
            layer=Layer.LIBRARY,
        ),
    )  # fmt: skip
    Works(store_db).remove_library_item("LIBM0001")  # the Resolution keeps its Work row

    text = api.get(f"/works/{villiger.work}").json()

    assert text["library_matches"] == []
    assert not any(c["library_match"] for b in text["blocks"] for c in b["citations"])


def _citing_work(
    conn: Conn,
    key: str,
    cited: WorkId,
    *,
    year: int | None = 2020,
    title: str | None = None,
    links: Sequence[float] = (),
    origin: TextOrigin = TextOrigin.PRINTED,
) -> WorkId:
    """A held Work with a firm Resolution naming `cited`, with a Citation per link's confidence."""
    work = Works(conn).ensure(key)
    Works(conn).record_library_item(work, LibraryItem(key=key, title=title or key, year=year))
    reference = Reference(id="b0", text="Printed", page=1)
    block = Block(kind=BlockKind.PARAGRAPH, heading_path=[], page=1, boxes=[], text="Some text.")
    WorkTexts(conn).replace(
        work, ParsedWork(text_origin=origin, blocks=[block], references=[reference])
    )
    Resolutions(conn).save(
        work,
        Resolution(
            reference="b0", state=ResolutionState.RESOLVED, work=cited, level=Level.CERTAIN,
            layer=Layer.LIBRARY,
        ),
    )  # fmt: skip
    citations = [
        Citation(
            block=0, span=Span(start=i, end=i + 1), text="[1]", citing_sentence=f"Sentence {i}.",
            link=CitationLink(reference_id="b0", confidence=c, evidence=[Evidence.LABEL]),
        )
        for i, c in enumerate(links)
    ]  # fmt: skip
    CitationGraphs(conn).replace(work, CitationGraph(references=[reference], citations=citations))
    return work


def test_a_work_is_cited_by_the_held_works_naming_it_newest_first(
    api: TestClient, store_db: Conn
) -> None:
    cited = _library_work(store_db, "CITED111", "The cited Work")
    _citing_work(store_db, "OLD11111", cited, year=2001, links=[1.0])
    _citing_work(store_db, "NOYEAR11", cited, year=None, links=[1.0])
    _citing_work(store_db, "NEWB1111", cited, year=2022, title="B", links=[1.0])
    _citing_work(store_db, "NEWA1111", cited, year=2022, title="A", links=[1.0])

    response = api.get(f"/works/{cited}/cited-by")

    assert response.status_code == 200
    assert [c["work"]["zotero_key"] for c in response.json()] == [
        "NEWA1111", "NEWB1111", "OLD11111", "NOYEAR11",
    ]  # fmt: skip


def test_cited_by_gives_each_citing_sentence_in_reading_order_with_its_level(
    api: TestClient, store_db: Conn
) -> None:
    cited = _library_work(store_db, "CITED111", "The cited Work")
    _citing_work(store_db, "CITER111", cited, links=[1.0, 0.1])

    [citing] = api.get(f"/works/{cited}/cited-by").json()

    assert [(c["citing_sentence"], c["confidence_level"]) for c in citing["citations"]] == [
        ("Sentence 0.", "certain"),
        ("Sentence 1.", "low"),
    ]
    assert all(isinstance(c["id"], int) for c in citing["citations"])
    assert citing["ocr"] is False


def test_a_citing_work_with_no_linked_citation_is_listed_without_sentences(
    api: TestClient, store_db: Conn
) -> None:
    cited = _library_work(store_db, "CITED111", "The cited Work")
    _citing_work(store_db, "CITER111", cited)

    [citing] = api.get(f"/works/{cited}/cited-by").json()

    assert citing["work"]["zotero_key"] == "CITER111"
    assert citing["citations"] == []


def test_a_citing_work_of_ocr_text_says_so(api: TestClient, store_db: Conn) -> None:
    cited = _library_work(store_db, "CITED111", "The cited Work")
    _citing_work(store_db, "SCAN1111", cited, links=[1.0], origin=TextOrigin.OCR)

    [citing] = api.get(f"/works/{cited}/cited-by").json()

    assert citing["ocr"] is True


def test_a_citing_work_no_longer_held_is_not_listed(api: TestClient, store_db: Conn) -> None:
    cited = _library_work(store_db, "CITED111", "The cited Work")
    _citing_work(store_db, "GONE1111", cited, links=[1.0])
    Works(store_db).remove_library_item("GONE1111")

    assert api.get(f"/works/{cited}/cited-by").json() == []


def test_cited_by_of_an_unknown_work_is_not_found(api: TestClient) -> None:
    assert api.get("/works/999999/cited-by").status_code == 404
