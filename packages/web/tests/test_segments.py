from paperexplorer_api.schemas import CitationSpan, ConfidenceLevel
from paperexplorer_web.segments import segments


def _span(id_: int, start: int, end: int, level: ConfidenceLevel = "high") -> CitationSpan:
    return CitationSpan(id=id_, start=start, end=end, confidence_level=level)


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
