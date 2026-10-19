from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paperexplorer.cli import app
from paperexplorer.config import Settings
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


def test_tex_split_accents_are_composed_with_their_letter(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("felippe2021-arxiv")

    first = next(r for r in work.references if r.label == "1")
    assert first.text == "Albert, R. and Barabási, A. Rev. Mod. Phys. 74, 47 (2002)."
    ethics = _block_with(work, "Ribeir")
    assert "University of S\u00e3o Paulo at Ribeir\u00e3o Preto" in ethics.text


def test_unnumbered_headings_nest_by_printed_size(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("villiger2024-tandf")

    paragraph = _block_with(work, "It is recommended and often required for")
    assert paragraph.heading_path == [
        "The value of therapists\u2019 personal experience",
        "Personal experience with psychotherapy",
    ]


def test_line_break_hyphens_are_joined_without_a_trace(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("villiger2024-tandf")

    paragraph = _block_with(work, "It is recommended and often required for")
    assert "required for psychotherapists to have personal experience" in paragraph.text


def test_page_only_pdf_link_keeps_just_the_target_page(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("albantakis2023-plos")

    paragraph = _block_with(work, "in objective terms [1].")
    marker = next(m for m in paragraph.citation_markers if m.text == "[1]")
    assert marker.link_target is not None
    assert (marker.link_target.page, marker.link_target.reference_id) == (42, None)
    cited = next(r for r in work.references if r.id == marker.grobid_reference_id)
    assert cited.label == "1"
    assert cited.text.startswith("Ellia F, Hendren J, Grasso M")


def test_blocks_carry_their_sentences(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("felippe2021-arxiv")

    paragraph = _block_with(work, f"arbitrary data set {RANGE_1_TO_3}")
    marker_at = paragraph.text.index(RANGE_1_TO_3)
    sentence = next(s for s in paragraph.sentences if s.start <= marker_at < s.end)
    assert paragraph.text[sentence.start : sentence.end] == (
        "An important open problem in many fields concerns how to estimate the entropy from the "
        f"Pearson correlation matrix of an arbitrary data set {RANGE_1_TO_3}."
    )


def test_scanned_pdf_is_read_by_ocr_and_marked_as_such(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("scan-villiger2024")

    assert work.text_origin is TextOrigin.OCR
    paragraph = _block_with(work, "impressive comeback")
    assert "Psychedelics have made an impressive comeback in mental health care." in paragraph.text
    assert paragraph.ocr_confidence is not None
    assert paragraph.ocr_confidence > 0.8


def test_scan_bibliography_is_split_into_one_reference_per_entry(
    parse_fixture: ParseFixture,
) -> None:
    work = parse_fixture("scan-villiger2024")

    texts = [r.text for r in work.references]  # OCR Text: compare what OCR reliably reads
    assert any(t.startswith("Beauchamp, T. L., & Childress, J.") for t in texts)
    assert any(t.startswith("Bender, D., & Hellerstein, D. J. (2022). Assessing") for t in texts)
    bogenschutz = next(t for t in texts if t.startswith("Bogenschutz, M. P"))
    assert "Forcehimes, A. A., Pommy, J. A" in bogenschutz  # words keep their printed order
    day = next(
        r
        for r in work.references
        if r.text.startswith("Carhart-Harris, R. L., Bolstridge, M., Day")
    )
    assert "Rucker, J., Day" not in day.text  # the next entry is its own Reference
    assert not any("Childress" in b.text for b in work.blocks)  # References are not body text


def test_scan_citation_markers_are_found_by_pattern(parse_fixture: ParseFixture) -> None:
    work = parse_fixture("scan-villiger2024")

    paragraph = _block_with(work, "major depressive disorder")
    cues = [m.cue or "" for m in paragraph.citation_markers]
    first_four = [(c.split(",")[0].split(" ")[0], c[-4:]) for c in cues[:4]]
    assert first_four == [
        ("Carhart-Harris", "2016"),
        ("Carhart-Harris", "2018"),
        ("Carhart-Harris", "2021"),  # a bare year after an author-year carries that author
        ("Goodwin", "2022"),
    ]
    spans = [paragraph.text[m.span.start : m.span.end] for m in paragraph.citation_markers[1:3]]
    assert spans[1] == "2021"


def test_cli_parse_prints_what_was_found(
    recorded_settings: Settings,
    fixture_pdf: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PE_DATA_DIR", str(recorded_settings.data_dir))
    monkeypatch.setenv("PE_GROBID_URL", recorded_settings.grobid_url)

    result = CliRunner().invoke(app, ["parse", str(fixture_pdf("villiger2024-tandf"))])

    assert result.exit_code == 0, result.output
    assert "printed text" in result.stdout
    assert "References: 80" in result.stdout  # GROBID's 79, one of them two merged entries
    assert "Citations: 90" in result.stdout


def test_marker_starting_inside_a_word_is_trimmed_to_the_next_word(
    parse_fixture: ParseFixture,
) -> None:
    work = parse_fixture("villiger2024-tandf")

    marker = next(
        m
        for b in work.blocks
        for m in b.citation_markers
        if "de Vos et al., 2016, p. 207" in m.text
    )
    assert marker.text == "(de Vos et al., 2016, p. 207)"


def test_author_and_year_links_of_one_citation_make_one_marker(parse_fixture: ParseFixture) -> None:
    # Regression: LaTeX links the author part and the year of `(Amigó et al, 2004)` separately;
    # the year's link stayed a second marker for the same Reference.
    work = parse_fixture("wegner2023-rsquare")

    paragraph = _block_with(work, "(Amigó et al, 2004). Sur")
    start = paragraph.text.index("(Amigó et al, 2004)")
    inside = [m for m in paragraph.citation_markers if start <= m.span.start < start + 19]
    assert [m.text for m in inside] == ["(Amigó et al, 2004)"]
    assert _linked_reference_text(work, inside[0]).startswith("Amigó JM, Szczepański J")
    for block in work.blocks:
        markers = block.citation_markers
        for i, a in enumerate(markers):
            for b in markers[i + 1 :]:
                same = a.link_target is not None and a.link_target == b.link_target
                assert not (same and a.span.start < b.span.end and b.span.start < a.span.end)


def test_a_bare_year_after_an_author_year_keeps_its_own_reference(
    parse_fixture: ParseFixture,
) -> None:
    work = parse_fixture("wegner2023-rsquare")

    paragraph = _block_with(work, "LZ-78 algorithms (Lempel")
    start = paragraph.text.index("(Lempel and Ziv, 1976, 1977;")
    inside = [m for m in paragraph.citation_markers if start <= m.span.start < start + 28]
    assert [(m.text, _linked_reference_text(work, m)[:21]) for m in inside] == [
        ("(Lempel and Ziv, 1976,", "Lempel A, Ziv J (1976"),
        ("1977;", "Lempel A, Ziv J (1977"),
    ]
