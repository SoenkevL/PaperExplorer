from collections.abc import Callable

from paperexplorer.citations import Citation, CitationGraph, Evidence, extract
from paperexplorer.parsing import (
    Block,
    BlockKind,
    CitationMarker,
    ParsedWork,
    Reference,
    Span,
    TextOrigin,
)

ParseFixture = Callable[[str], ParsedWork]


def _graph(parse_fixture: ParseFixture, name: str) -> CitationGraph:
    return extract(parse_fixture(name))


def _reference_text(graph: CitationGraph, citation: Citation) -> str:
    assert citation.link is not None
    return next(r.text for r in graph.references if r.id == citation.link.reference_id)


def test_exact_pdf_link_outranks_grobid(parse_fixture: ParseFixture) -> None:
    graph = _graph(parse_fixture, "villiger2024-tandf")

    citation = next(c for c in graph.citations if c.text == "2021")  # GROBID: Nielson (2021)
    assert citation.link is not None
    assert _reference_text(graph, citation).startswith("Carhart-Harris, R. L., Giribaldi, B.")
    assert citation.link.confidence == 1.0
    assert Evidence.PDF_LINK in citation.link.evidence


def test_range_interior_is_linked_by_its_printed_label(parse_fixture: ParseFixture) -> None:
    doc = parse_fixture("felippe2021-arxiv")
    graph = extract(doc)

    printed = "[1\u20133]"
    block = next(i for i, b in enumerate(doc.blocks) if f"arbitrary data set {printed}" in b.text)
    start = doc.blocks[block].text.index(printed)
    in_range = [
        c
        for c in graph.citations
        if c.block == block and start <= c.span.start < start + len(printed)
    ]
    linked = {_reference_text(graph, c)[:20]: c for c in in_range}
    assert sorted(linked) == [
        "Albert, R. and Barab",
        "Anand, K. and Bianco",
        "De Domenico, M. and ",
    ]
    middle = linked["Anand, K. and Bianco"].link
    assert middle is not None
    assert middle.evidence == [Evidence.LABEL]
    assert middle.confidence < 1.0


def test_grobid_and_label_agreeing_give_a_high_confidence_link(parse_fixture: ParseFixture) -> None:
    doc = parse_fixture("albantakis2023-plos")
    graph = extract(doc)

    block = next(i for i, b in enumerate(doc.blocks) if "in objective terms [1]." in b.text)
    citation = next(c for c in graph.citations if c.block == block and c.text == "[1]")
    assert citation.link is not None
    assert _reference_text(graph, citation).startswith("Ellia F, Hendren J, Grasso M")
    assert set(citation.link.evidence) == {Evidence.GROBID, Evidence.LABEL}
    assert citation.link.confidence >= 0.9


def test_scan_citations_are_linked_by_surname_and_year(parse_fixture: ParseFixture) -> None:
    graph = _graph(parse_fixture, "scan-villiger2024")

    first = next(c for c in graph.citations if c.text.startswith("Carhart-Harris et al"))
    assert first.text.endswith("2018")
    assert _reference_text(graph, first).startswith("Carhart-Harris, R. L., Bolstridge, M., Day")
    assert first.link is not None
    assert first.link.evidence == [Evidence.AUTHOR_YEAR]
    bare_year = next(c for c in graph.citations if c.text == "2021")
    assert _reference_text(graph, bare_year).startswith("Carhart-Harris, R. L., Giribaldi")


def test_citing_sentence_is_the_sentence_without_its_markers(parse_fixture: ParseFixture) -> None:
    graph = _graph(parse_fixture, "felippe2021-arxiv")

    citation = next(c for c in graph.citations if c.text.startswith("[1"))
    assert citation.citing_sentence == (
        "An important open problem in many fields concerns how to estimate the entropy from the "
        "Pearson correlation matrix of an arbitrary data set."
    )


def test_scan_citing_sentences_split_at_sentence_ends(parse_fixture: ParseFixture) -> None:
    graph = _graph(parse_fixture, "scan-villiger2024")

    # OCR drops "(DMT).1" before this sentence, so only robust properties are checked.
    sentence = next(c for c in graph.citations if c.text == "2021").citing_sentence
    assert "efficacy in the treatment of major depressive disorder" in sentence
    assert "2021" not in sentence
    assert "Carhart-Harris" not in sentence
    assert sentence.endswith(".")


def test_citation_without_evidence_is_reported_as_unlinked() -> None:
    text = "Nothing is known about this (Nobody et al., 1999), but Smith (2020) disagrees."
    nobody = text.index("Nobody")
    smith = text.index("Smith")
    doc = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            Block(
                kind=BlockKind.PARAGRAPH,
                heading_path=[],
                page=1,
                boxes=[],
                text=text,
                citation_markers=[
                    CitationMarker(
                        span=Span(start=nobody, end=nobody + 19),
                        text="Nobody et al., 1999",
                        cue="Nobody et al., 1999",
                    ),
                    CitationMarker(
                        span=Span(start=smith, end=smith + 12),
                        text="Smith (2020)",
                        cue="Smith (2020)",
                    ),
                ],
            )
        ],
        references=[Reference(id="b0", text="Smith, J. (2020). A title. A Journal.", page=9)],
    )

    graph = extract(doc)

    assert [c.text for c in graph.unlinked()] == ["Nobody et al., 1999"]
    assert [c.text for c in graph.low_confidence()] == ["Smith (2020)"]  # a rule alone
