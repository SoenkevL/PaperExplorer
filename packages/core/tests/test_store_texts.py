from collections.abc import Callable

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.parsing import ParsedWork, TextOrigin
from paperexplorer.store import Works, WorkTexts

Conn = psycopg.Connection[TupleRow]
ParseFixture = Callable[[str], ParsedWork]


@pytest.mark.parametrize("name", ["villiger2024-tandf", "scan-villiger2024"])
def test_a_parsed_work_comes_back_exactly_as_stored(
    store_db: Conn, parse_fixture: ParseFixture, name: str
) -> None:
    parsed = parse_fixture(name)
    work = Works(store_db).ensure(name)

    WorkTexts(store_db).replace(work, parsed)

    assert WorkTexts(store_db).get(work) == parsed


def test_the_work_records_how_its_text_was_read(
    store_db: Conn, parse_fixture: ParseFixture
) -> None:
    works = Works(store_db)
    work = works.ensure("scan")

    WorkTexts(store_db).replace(work, parse_fixture("scan-villiger2024"))

    stored = works.get(work)
    assert stored is not None
    assert stored.text_origin is TextOrigin.OCR


def test_parsing_again_replaces_the_text_instead_of_adding_to_it(
    store_db: Conn, parse_fixture: ParseFixture
) -> None:
    texts = WorkTexts(store_db)
    work = Works(store_db).ensure("villiger")
    parsed = parse_fixture("villiger2024-tandf")
    texts.replace(work, parsed)
    shorter = parsed.model_copy(
        update={"blocks": parsed.blocks[:3], "references": parsed.references[:2]}
    )

    texts.replace(work, shorter)

    assert texts.get(work) == shorter


def test_a_work_not_yet_parsed_has_no_text(store_db: Conn) -> None:
    assert WorkTexts(store_db).get(Works(store_db).ensure("unparsed")) is None
