from paperexplorer_api.schemas import CitationSpan, ConfidenceLevel
from paperexplorer_web.segments import segments


def _span(
    id_: int, start: int, end: int, level: ConfidenceLevel = "high", *, held: bool = False
) -> CitationSpan:
    return CitationSpan(id=id_, start=start, end=end, confidence_level=level, library_match=held)


def test_text_without_citations_is_one_plain_segment() -> None:
    [only] = segments("No markers here.", [])

    assert only.text == "No markers here."
    assert only.citations == []


def test_a_citation_becomes_its_own_segment_between_plain_text() -> None:
    text = "As shown (Smith, 2020), entropy rises."

    parts = segments(text, [_span(7, 9, 22, "certain")])

    assert [(p.text, p.citations, p.confidence_level) for p in parts] == [
        ("As shown ", [], None),
        ("(Smith, 2020)", [7], "certain"),
        (", entropy rises.", [], None),
    ]


def test_citations_sharing_one_printed_dash_share_one_segment() -> None:
    text = "data [1\u20133]."  # the dash stands for Citation 2, and GROBID gives it to 1-3 too

    parts = segments(text, [_span(1, 5, 7), _span(2, 7, 8, "low"), _span(3, 7, 8), _span(4, 8, 10)])

    assert [(p.text, p.citations) for p in parts] == [
        ("data ", []),
        ("[1", [1]),
        ("\u2013", [2, 3]),
        ("3]", [4]),
        (".", []),
    ]
    assert parts[2].confidence_level == "low"  # the weakest link of the segment


def test_a_nested_citation_splits_the_outer_one_and_joins_its_text_back() -> None:
    text = "see (Amig\u00f3 et al, 2004) for"

    parts = segments(text, [_span(1, 4, 23, "certain"), _span(2, 18, 23, "unlinked")])

    assert "".join(p.text for p in parts) == text
    assert [(p.text, p.citations, p.confidence_level) for p in parts] == [
        ("see ", [], None),
        ("(Amig\u00f3 et al, ", [1], "certain"),
        ("2004)", [1, 2], "unlinked"),
        (" for", [], None),
    ]


def test_a_printed_range_is_a_library_match_when_any_firm_citation_in_it_is() -> None:
    text = "data [1\u20133]."  # the dash stands for Citation 2; only 2 is a Library Match
    spans = [_span(1, 5, 7), _span(2, 7, 8, held=True), _span(3, 7, 10)]  # 3 also holds the dash

    parts = segments(text, spans)

    assert [(p.text, p.library_match) for p in parts] == [
        ("data ", False), ("[1", False), ("\u2013", True), ("3]", False), (".", False),
    ]  # fmt: skip


def test_a_printed_range_with_a_low_link_keeps_its_warning_over_a_library_match() -> None:
    text = "data [1\u20133]."
    spans = [_span(2, 7, 8, held=True), _span(3, 7, 8, "low")]

    [dash] = [p for p in segments(text, spans) if p.citations]

    assert (dash.confidence_level, dash.library_match) == ("low", False)
