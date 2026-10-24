from collections.abc import Callable

import pytest

from paperexplorer.citations import (
    Citation,
    CitationGraph,
    CitationLink,
    Evidence,
    confidence_level,
    extract,
)
from paperexplorer.parsing import (
    Block,
    BlockKind,
    CitationMarker,
    LinkTarget,
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


def test_author_count_in_the_cue_tells_same_year_entries_apart() -> None:
    text = "One view (Yaden and Griffiths, 2021) and another (Yaden et al., 2021)."
    two, many = text.index("Yaden and"), text.index("Yaden et")
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
                        span=Span(start=two, end=two + 25),
                        text="Yaden and Griffiths, 2021",
                        cue="(Yaden and Griffiths, 2021)",
                    ),
                    CitationMarker(
                        span=Span(start=many, end=many + 18),
                        text="Yaden et al., 2021",
                        cue="(Yaden et al., 2021)",
                    ),
                ],
            )
        ],
        references=[
            Reference(
                id="b0", text="Yaden, D.B., Griffiths, R.R., 2021. The subjective effects.", page=9
            ),
            Reference(
                id="b1",
                text="Yaden, D.B., Johnson, M.W., Griffiths, R.R., Doss, M.K., 2021. Psychedelics.",
                page=9,
            ),
        ],
    )

    graph = extract(doc)

    assert [c.link.reference_id if c.link else None for c in graph.citations] == ["b0", "b1"]


def test_a_grobid_link_to_an_entry_by_other_authors_is_dropped() -> None:
    # Regression, the Seed: GROBID linked `(Carhart-Harris, 2025)` to b66, the entry it had
    # merged with the next one; split, b66 is Huxley 1959. The PDF's own link points at page 14,
    # where the entry the authors mean is printed without a year. Markers as parsed, 2026-10-24.
    text = "One view (Carhart-Harris, 2025) and a wider one (Carhart-Harris et al., 2025)."
    one, many = text.index("(Carhart-Harris, 2025)"), text.index("(Carhart-Harris et al.")
    doc = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            Block(
                kind=BlockKind.PARAGRAPH,
                heading_path=[],
                page=3,
                boxes=[],
                text=text,
                citation_markers=[
                    CitationMarker(
                        span=Span(start=one, end=one + 22),
                        text="(Carhart-Harris, 2025)",
                        cue="(Carhart-Harris, 2025)",
                        grobid_reference_id="b66",
                        link_target=LinkTarget(page=14),
                    ),
                    CitationMarker(
                        span=Span(start=many, end=many + 29),
                        text="(Carhart-Harris et al., 2025)",
                        cue="(Carhart-Harris et al., 2025)",
                        grobid_reference_id="b66",
                    ),
                ],
            )
        ],
        references=[
            Reference(
                id="b17",
                text="Carhart-Harris, R.L., 2018. The entropic brain - revisited. "
                "Neuropharmacology 142, 167\u2013178.",
                page=14,
                surnames=["Carhart-Harris"],
                year="2018",
            ),
            Reference(
                id="b18",
                text="Carhart-Harris, R. The entropic brain today. OSF Preprint.",
                page=14,
                surnames=["Carhart-Harris"],
            ),
            Reference(
                id="b66",
                text="Huxley, A., 1959. The doors of perception: And heaven and hell. "
                "Random House.",
                page=15,
                surnames=["Huxley"],
                year="1959",
            ),
            Reference(
                id="b66-2",
                text="Irrmischer, M., Aqil, M., Luan, L., Wang, T., Engelbregt, H., "
                "Carhart-Harris, R., Linkenkaer-Hansen, K., Timmermann, C., 2025. "
                "DMT-induced shifts in criticality correlate with self-dissolution.",
                page=15,
                surnames=["Irrmischer"],
                year="2025",
            ),
        ],
    )

    graph = extract(doc)

    assert [c.link for c in graph.citations] == [None, None]


@pytest.mark.parametrize(
    ("link", "level"),
    [
        (CitationLink(reference_id="b1", confidence=1.0, evidence=[Evidence.PDF_LINK]), "certain"),
        (
            CitationLink(
                reference_id="b1", confidence=0.9, evidence=[Evidence.GROBID, Evidence.LABEL]
            ),
            "high",
        ),
        (CitationLink(reference_id="b1", confidence=0.6, evidence=[Evidence.LABEL]), "low"),
        (None, "unlinked"),
    ],
)
def test_a_citation_link_is_certain_high_low_or_missing(
    link: CitationLink | None, level: str
) -> None:
    assert confidence_level(link) == level
