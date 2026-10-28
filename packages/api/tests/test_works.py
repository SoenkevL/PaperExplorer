from collections import Counter
from collections.abc import Callable
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.domain import LibraryItem
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import Works

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
        "fetch": "done", "parse": "done", "chunk": "done", "extract_citations": "done",
    }  # fmt: skip


def test_a_stage_that_never_ran_is_pending(api: TestClient, store_db: Conn) -> None:
    Works(store_db).ensure("NEW23456")

    [work] = api.get("/works").json()

    assert work["stages"] == {
        "fetch": "pending", "parse": "pending", "chunk": "pending", "extract_citations": "pending",
    }  # fmt: skip
    assert work["text_origin"] is None


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
