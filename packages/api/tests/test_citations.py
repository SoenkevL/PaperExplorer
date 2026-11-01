from collections.abc import Callable
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.citations import extract
from paperexplorer.domain import LibraryItem
from paperexplorer.parsing import ParsedWork
from paperexplorer.resolution import InMemoryDoiRegistry, verify
from paperexplorer.store import CitationGraphs, Works, WorkTexts

from .conftest import Stored

Conn = psycopg.Connection[TupleRow]


def _citations(api: TestClient, work: int) -> list[dict[str, Any]]:
    blocks = api.get(f"/works/{work}").json()["blocks"]
    return [c for b in blocks for c in b["citations"]]


def test_a_citation_gives_its_reference_and_the_link_to_it(
    api: TestClient, villiger: Stored
) -> None:
    span = next(c for c in _citations(api, villiger.work) if c["confidence_level"] == "certain")

    response = api.get(f"/citations/{span['id']}")

    assert response.status_code == 200
    detail = response.json()
    index = [c["id"] for c in _citations(api, villiger.work)].index(span["id"])
    citation = villiger.graph.citations[index]
    assert citation.link is not None
    reference = next(r for r in villiger.parsed.references if r.id == citation.link.reference_id)
    assert detail["id"] == span["id"]
    assert detail["work_id"] == villiger.work
    assert detail["text"] == citation.text
    assert detail["citing_sentence"] == citation.citing_sentence
    assert detail["block"] == citation.block
    assert detail["text_origin"] == "printed"
    assert detail["link"] == {
        "confidence": 1.0,
        "evidence": [e.value for e in citation.link.evidence],
        "confidence_level": "certain",
    }
    assert detail["reference"] == {
        "key": reference.id,
        "label": reference.label,
        "text": reference.text,
        "page": reference.page,
        "surnames": reference.surnames,
        "year": reference.year,
        "title": reference.title,
        "doi": reference.doi,
        "doi_registration": None,
    }


def test_an_unlinked_citation_has_neither_link_nor_reference(
    api: TestClient, store_db: Conn, parse_fixture: Callable[[str], ParsedWork]
) -> None:
    parsed = parse_fixture("villiger2024-tandf")
    work = Works(store_db).ensure("VILL2024")
    WorkTexts(store_db).replace(work, parsed)
    graph = extract(parsed)
    graph.citations[0].link = None
    CitationGraphs(store_db).replace(work, graph)
    first = _citations(api, work)[0]

    detail = api.get(f"/citations/{first['id']}").json()

    assert first["confidence_level"] == "unlinked"
    assert detail["link"] is None
    assert detail["reference"] is None


def test_a_reference_says_whether_doi_org_has_its_doi(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    refs = {r.id: r for r in villiger.parsed.references}
    index, ref = next(
        (i, refs[c.link.reference_id])
        for i, c in enumerate(villiger.graph.citations)
        if c.link and refs[c.link.reference_id].doi
    )
    # A registry that knows none of the DOIs it is asked about.
    verify(store_db, InMemoryDoiRegistry(), [ref])
    span = _citations(api, villiger.work)[index]

    detail = api.get(f"/citations/{span['id']}").json()

    assert detail["reference"]["doi_registration"] == "unregistered"


def test_an_unknown_citation_is_not_found(api: TestClient) -> None:
    assert api.get("/citations/999999").status_code == 404


def test_a_citation_in_ocr_text_says_so(
    api: TestClient,
    store_work: Callable[[LibraryItem, ParsedWork], Stored],
    parse_fixture: Callable[[str], ParsedWork],
) -> None:
    scan = store_work(LibraryItem(key="SCAN2345"), parse_fixture("scan-villiger2024"))
    first = _citations(api, scan.work)[0]

    assert api.get(f"/citations/{first['id']}").json()["text_origin"] == "ocr"
