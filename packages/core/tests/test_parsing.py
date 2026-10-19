from collections.abc import Callable

from paperexplorer.parsing import Block, CitationMarker, ParsedWork, TextOrigin

ParseFixture = Callable[[str], ParsedWork]
RANGE_1_TO_3 = "[1\u20133]"  # as printed, with an en dash


def _block_with(work: ParsedWork, text: str) -> Block:
    return next(b for b in work.blocks if text in b.text)


def _linked_reference_text(work: ParsedWork, marker: CitationMarker) -> str:
    assert marker.link_target is not None
    assert marker.link_target.reference_id is not None
    return next(r.text for r in work.references if r.id == marker.link_target.reference_id)


def test_printed_pdf_keeps_numbered_citation_range_as_printed(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("felippe2021-arxiv")

    assert work.text_origin is TextOrigin.PRINTED
    paragraph = next(b for b in work.blocks if "arbitrary data set" in b.text)
    assert (
        "how to estimate the entropy from the Pearson correlation matrix of an arbitrary data set "
        f"{RANGE_1_TO_3}." in paragraph.text
    )


def test_citation_marker_spans_its_printed_text_and_names_its_reference(
    parse_fixture: ParseFixture,
) -> None:
    work = parse_fixture("villiger2024-tandf")

    paragraph = _block_with(work, "Psychedelics have made an impressive")
    marker = next(m for m in paragraph.citation_markers if m.text.startswith("Carhart-Harris et"))
    assert paragraph.text[marker.span.start : marker.span.end] == marker.text
    assert marker.text == "Carhart-Harris et al., 2018"  # any whitespace prints as one space
    references = {r.id: r for r in work.references}
    assert marker.grobid_reference_id is not None
    assert references[marker.grobid_reference_id].text.startswith(
        "Carhart-Harris, R. L., Bolstridge, M., Day, C. M. J., Rucker, J."
    )


def test_pdf_link_names_the_reference_where_grobid_has_none_or_the_wrong_one(
    parse_fixture: ParseFixture,
) -> None:
    work = parse_fixture("villiger2024-tandf")

    paragraph = _block_with(work, "Psychedelics have made an impressive")
    linked = [m for m in paragraph.citation_markers if m.link_target is not None]
    first_2016 = next(m for m in linked if "2016" in m.text)
    first_2021 = next(m for m in linked if "2021" in m.text)
    assert first_2016.text == "(Carhart-Harris, Bolstridge, et al., 2016;"
    # GROBID leaves the first "2016" unlinked and points the first "2021" at Nielson (2021).
    assert _linked_reference_text(work, first_2016).startswith(
        "Carhart-Harris, R. L., Bolstridge, M., Rucker, J., Day, C. M. J."
    )
    assert _linked_reference_text(work, first_2021).startswith(
        "Carhart-Harris, R. L., Giribaldi, B., Watts, R., Baker-Jones,"
    )


def test_numbered_range_endpoints_carry_their_pdf_link_targets(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("felippe2021-arxiv")

    paragraph = _block_with(work, f"of an arbitrary data set {RANGE_1_TO_3}")
    start = paragraph.text.index(RANGE_1_TO_3)
    end = start + len(RANGE_1_TO_3)
    in_range = [m for m in paragraph.citation_markers if start <= m.span.start < end]
    targets = [_linked_reference_text(work, m) for m in in_range if m.link_target is not None]
    assert [t[:20] for t in targets] == ["Albert, R. and Barab", "De Domenico, M. and "]
