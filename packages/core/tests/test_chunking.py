from collections.abc import Callable
from itertools import pairwise
from math import ceil

import pytest

from paperexplorer.chunking import MAX_TOKENS, SHORT_TOKENS, chunk
from paperexplorer.domain import ChunkFlag
from paperexplorer.parsing import Block, BlockKind, Box, ParsedWork, Span, TextOrigin

ParseFixture = Callable[[str], ParsedWork]

SENTENCE = "Sample entropy of the EEG rose after the first dose in every participant."
LONG_SENTENCE = " ".join(
    ["the signal's entropy rose", "while its spectral power fell", "across all electrodes"] * 70
)
BOX = Box(page=3, x=72.0, y=100.0, width=450.0, height=12.0)


def _block(
    sentences: list[str],
    kind: BlockKind = BlockKind.PARAGRAPH,
    heading_path: list[str] | None = None,
    spans: bool = True,
) -> Block:
    """A block of `sentences` joined by spaces, with their spans unless `spans` is off."""
    text = " ".join(sentences)
    starts = [sum(len(s) + 1 for s in sentences[:i]) for i in range(len(sentences))]
    return Block(
        kind=kind,
        heading_path=heading_path or [],
        page=3,
        boxes=[BOX],
        text=text,
        sentences=[Span(start=a, end=a + len(s)) for a, s in zip(starts, sentences, strict=True)]
        if spans
        else [],
    )


def _doc(*blocks: Block, origin: TextOrigin = TextOrigin.PRINTED) -> ParsedWork:
    return ParsedWork(text_origin=origin, blocks=list(blocks), references=[])


def _tokens(sentence: str) -> int:
    return sum(c.tokens for c in chunk(_doc(_block([sentence]))))


def test_a_paragraph_is_one_chunk_that_keeps_its_place_and_sentences() -> None:
    heading = _block(["2.1 Data"], kind=BlockKind.HEADING, heading_path=["2 Methods"])
    paragraph = _block([SENTENCE, SENTENCE], heading_path=["2 Methods", "2.1 Data"])

    [only] = chunk(_doc(heading, paragraph))

    assert only.position == 0
    assert only.block == 1
    assert only.span == Span(start=0, end=len(paragraph.text))
    assert only.text == paragraph.text
    assert only.sentences == paragraph.sentences
    assert only.sentence_texts() == [SENTENCE, SENTENCE]
    assert only.heading_path == ["2 Methods", "2.1 Data"]
    assert (only.page, only.boxes) == (3, [BOX])
    assert only.tokens == 2 * _tokens(SENTENCE)
    assert only.flags == []
    assert only.text_origin is TextOrigin.PRINTED


def test_headings_are_not_chunks_but_footnotes_are() -> None:
    doc = _doc(
        _block(["Introduction"], kind=BlockKind.HEADING),
        _block([SENTENCE]),
        _block([SENTENCE], kind=BlockKind.FOOTNOTE),
    )

    assert [(c.position, c.block) for c in chunk(doc)] == [(0, 1), (1, 2)]


def test_a_long_paragraph_splits_at_sentences_into_the_fewest_balanced_windows() -> None:
    per_sentence = _tokens(SENTENCE)
    fits = MAX_TOKENS // per_sentence
    n = 2 * fits + 10  # too many for two windows that share a sentence
    paragraph = _block([SENTENCE] * n)

    windows = chunk(_doc(paragraph))

    # Each window after the first adds all but its first sentence, which it shares.
    assert len(windows) == 1 + ceil((n - fits) / (fits - 1)) == 3
    assert all(w.tokens <= MAX_TOKENS for w in windows)
    sizes = [len(w.sentences) for w in windows]
    assert max(sizes) - min(sizes) <= 1
    for before, after in pairwise(windows):
        assert after.sentences[0] == before.sentences[-1]
        assert after.span.start == before.sentences[-1].start
    assert windows[0].span.start == 0
    assert windows[-1].span.end == len(paragraph.text)
    assert [w.position for w in windows] == [0, 1, 2]
    assert {w.block for w in windows} == {0}
    assert all(w.text == paragraph.text[w.span.start : w.span.end] for w in windows)


def test_a_sentence_over_the_limit_is_cut_at_tokens_and_flagged() -> None:
    total = _tokens(LONG_SENTENCE)
    paragraph = _block([SENTENCE, LONG_SENTENCE, SENTENCE])

    first, *pieces, last = chunk(_doc(paragraph))

    assert total > MAX_TOKENS
    assert len(pieces) == ceil(total / MAX_TOKENS)
    assert all(ChunkFlag.OVERSIZED_SENTENCE in p.flags for p in pieces)
    assert all(p.tokens <= MAX_TOKENS for p in pieces)
    assert sum(p.tokens for p in pieces) == total
    assert max(p.tokens for p in pieces) - min(p.tokens for p in pieces) <= 1
    long = paragraph.sentences[1]
    assert (pieces[0].span.start, pieces[-1].span.end) == (long.start, long.end)
    for before, after in pairwise(pieces):
        assert not paragraph.text[before.span.end : after.span.start].strip()
    assert first.text == last.text == SENTENCE
    assert ChunkFlag.OVERSIZED_SENTENCE not in first.flags + last.flags


def test_a_paragraph_under_30_tokens_is_its_own_short_chunk() -> None:
    doc = _doc(_block(["Psilocybin."]), _block(["LSD."]), _block([SENTENCE, SENTENCE]))

    chunks = chunk(doc)

    assert [c.text for c in chunks] == ["Psilocybin.", "LSD.", f"{SENTENCE} {SENTENCE}"]
    assert [c.flags for c in chunks] == [[ChunkFlag.SHORT], [ChunkFlag.SHORT], []]
    assert chunks[0].tokens < SHORT_TOKENS


def test_a_scan_splits_at_sentence_ends_and_keeps_its_origin() -> None:
    paragraph = _block([SENTENCE] * 40, spans=False)

    windows = chunk(_doc(paragraph, origin=TextOrigin.OCR))

    assert len(windows) == 2
    assert all(w.text_origin is TextOrigin.OCR for w in windows)
    assert all(t == SENTENCE for w in windows for t in w.sentence_texts())
    assert windows[1].sentences[0] == windows[0].sentences[-1]


def test_overlapping_sentence_spans_become_borders_that_never_overlap() -> None:
    text = "Its eigenvalues are known [8]. Since they are simply λ/N, we get the entropy."
    border = text.index("Since")
    block = Block(
        kind=BlockKind.PARAGRAPH,
        heading_path=[],
        page=1,
        boxes=[],
        text=text,
        sentences=[Span(start=0, end=border + 5), Span(start=border - 4, end=len(text))],
    )

    [only] = chunk(_doc(block))

    assert only.sentences == [Span(start=0, end=border + 5), Span(start=border + 6, end=len(text))]


def test_a_blank_paragraph_has_no_chunk() -> None:
    blank = Block(kind=BlockKind.PARAGRAPH, heading_path=[], page=1, boxes=[], text="  ")

    assert [c.block for c in chunk(_doc(blank, _block([SENTENCE])))] == [1]


@pytest.mark.parametrize(
    "name",
    [
        "albantakis2023-plos",
        "felippe2021-arxiv",
        "villiger2024-tandf",
        "wegner2023-rsquare",
        "scan-villiger2024",
    ],
)
def test_every_fixture_chunks_into_whole_paragraphs_within_the_limit(
    parse_fixture: ParseFixture, name: str
) -> None:
    doc = parse_fixture(name)

    chunks = chunk(doc)

    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert [c.block for c in chunks] == sorted(c.block for c in chunks)
    for c in chunks:
        block = doc.blocks[c.block]
        assert block.kind is not BlockKind.HEADING
        assert c.text == block.text[c.span.start : c.span.end] == c.text.strip()
        assert c.tokens <= MAX_TOKENS
        assert (ChunkFlag.SHORT in c.flags) == (c.tokens < SHORT_TOKENS)
        assert c.heading_path == block.heading_path
        assert c.text_origin is doc.text_origin
        assert all(c.span.start <= s.start < s.end <= c.span.end for s in c.sentences)
        assert all(a.end <= b.start for a, b in pairwise(c.sentences))
    for i, block in enumerate(doc.blocks):
        if block.kind is BlockKind.HEADING:
            continue
        covered = {p for c in chunks if c.block == i for p in range(c.span.start, c.span.end)}
        assert covered >= {p for p, ch in enumerate(block.text) if not ch.isspace()}
