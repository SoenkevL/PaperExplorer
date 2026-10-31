"""The answering agent's view of the Seed: its map and five tools, over real Postgres."""

import re

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer import retrieval
from paperexplorer.asking import Conversation, SeedTools
from paperexplorer.asking import tools as tools_module
from paperexplorer.domain import Scope, Work
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.store import Chunks, CitationGraphs, StoredChunk

from .conftest import ABSTRACT, DISCUSSION, INTRODUCTION, RECORDING, SESSIONS

Conn = psycopg.Connection[TupleRow]
# A Chunk as a tool shows it: `[#n] <Heading Path> · p. <page>`, then its text.
_SHOWN = re.compile(r"^\[#(\d+)\] (.+) · p\. (\d+)\n(.+)$", re.MULTILINE)


def shown(output: str) -> list[tuple[int, str, int, str]]:
    return [(int(n), path, int(page), text) for n, path, page, text in _SHOWN.findall(output)]


@pytest.fixture
def conversation(small_seed: Work) -> Conversation:
    return Conversation(work=small_seed)


@pytest.fixture
def seed_tools(store_db: Conn, conversation: Conversation) -> SeedTools:
    return SeedTools(store_db, FakeEmbedder(), conversation)


def chunk(conn: Conn, work: Work, text: str) -> StoredChunk:
    return next(c for c in Chunks(conn).for_work(work.id) if c.chunk.text == text)


def test_the_map_lists_the_sections_and_shows_the_abstract_with_its_handle(
    store_db: Conn, small_seed: Work, conversation: Conversation, seed_tools: SeedTools
) -> None:
    seed_map = seed_tools.seed_map()

    assert seed_map == (
        "The Seed: Entropy after psilocybin (A. Author, 2024)\n"
        "\n"
        "Its sections, for read_section (give the § number or the heading):\n"
        "§1 1 Introduction · p. 1\n"
        "§2 2 Methods · p. 2\n"
        "§3   2.1 Recording · p. 2\n"
        "§4 3 Discussion · p. 3\n"
        "\n"
        "Its text before the first heading, the abstract:\n"
        "[#1] (no heading) · p. 1\n"
        f"{ABSTRACT}"
    )
    abstract = chunk(store_db, small_seed, ABSTRACT)
    assert conversation.handles == {1: abstract}
    assert seed_tools.chunks_read() == [abstract]


def test_search_shows_the_best_chunks_with_handles_in_rank_order(
    store_db: Conn, small_seed: Work, conversation: Conversation, seed_tools: SeedTools
) -> None:
    question = "How was the entropy computed from each recording?"
    expected = retrieval.search(store_db, FakeEmbedder(), question, Scope.of(small_seed.id), k=3)

    output = seed_tools.search(question, k=3)

    results = shown(output)
    assert [text for _, _, _, text in results] == [r.chunk.text for r in expected]
    assert [n for n, _, _, _ in results] == [1, 2, 3]
    assert results[0][1:3] == ("2 Methods > 2.1 Recording", 2)
    assert [conversation.handles[n].id for n, _, _, _ in results] == [r.id for r in expected]
    assert seed_tools.chunks_read() == [conversation.handles[n] for n in (1, 2, 3)]


def test_a_chunk_keeps_its_handle_when_shown_again(seed_tools: SeedTools) -> None:
    seed_tools.seed_map()
    first = shown(seed_tools.search("brain entropy volunteers psilocybin"))

    again = shown(seed_tools.search("brain entropy volunteers psilocybin"))

    assert first == again
    assert (1, "(no heading)", 1, ABSTRACT) in again
    assert len(seed_tools.chunks_read()) == len(first)


def test_search_for_nothing_says_so(seed_tools: SeedTools) -> None:
    assert seed_tools.search("   ") == "Nothing in the Seed matches '   '."


@pytest.mark.parametrize("heading", ["§2", "2", "2 Methods", "methods", " §2 "])
def test_read_section_reads_the_chunks_under_a_heading_in_reading_order(
    seed_tools: SeedTools, heading: str
) -> None:
    output = seed_tools.read_section(heading)

    assert output.startswith("§2 2 Methods · p. 2, part 1 of 1\n\n")
    assert [text for _, _, _, text in shown(output)] == [RECORDING, SESSIONS]
    assert "read_section" not in output


def test_read_section_takes_a_full_heading_path(seed_tools: SeedTools) -> None:
    output = seed_tools.read_section("2 Methods > 2.1 Recording")

    assert output.startswith("§3 2 Methods > 2.1 Recording · p. 2, part 1 of 1")


def test_read_section_continues_in_parts_of_about_its_token_budget(
    seed_tools: SeedTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_module, "SECTION_TOKENS", 12)

    first = seed_tools.read_section("§2")
    second = seed_tools.read_section("§2", part=2)

    assert [text for _, _, _, text in shown(first)] == [RECORDING]
    assert first.endswith('\n\nThe section goes on: read_section("§2", part=2).')
    assert second.startswith("§2 2 Methods · p. 2, part 2 of 2\n\n")
    assert [text for _, _, _, text in shown(second)] == [SESSIONS]
    assert seed_tools.read_section("§2", part=3) == "§2 2 Methods has 2 parts."


def test_read_section_of_an_unknown_heading_points_at_the_map(seed_tools: SeedTools) -> None:
    assert seed_tools.read_section("Results") == (
        "The Seed has no section 'Results'. Give a § number or a heading from the map."
    )


def test_read_around_shows_a_chunk_with_its_neighbours(
    store_db: Conn, small_seed: Work, conversation: Conversation, seed_tools: SeedTools
) -> None:
    recording = conversation.handle(chunk(store_db, small_seed, RECORDING))

    output = seed_tools.read_around(f"#{recording}")

    assert [text for _, _, _, text in shown(output)] == [INTRODUCTION, RECORDING, SESSIONS]
    assert [n for n, _, _, text in shown(output) if text == RECORDING] == [recording]
    assert seed_tools.read_around(recording, n=2).count("\n[#") == 4


def test_read_around_an_unknown_handle_says_so(seed_tools: SeedTools) -> None:
    assert seed_tools.read_around("[#9]") == (
        "No Chunk has the handle [#9]. Use a handle a tool has shown you."
    )


def test_reference_gives_the_reference_as_printed_with_its_link_confidence(
    store_db: Conn, small_seed: Work, conversation: Conversation, seed_tools: SeedTools
) -> None:
    introduction = conversation.handle(chunk(store_db, small_seed, INTRODUCTION))
    link = next(
        c.citation.link
        for c in CitationGraphs(store_db).for_work(small_seed.id)
        if c.citation.text == "[2]"
    )
    assert link is not None

    output = seed_tools.reference(introduction, "[2]")

    assert output == (
        f"[2] in [#{introduction}] points at Reference b1"
        f" (Citation Link confidence {link.confidence:.2f}, high), as printed:\n"
        "2. Smith J. Scalp recordings of entropy. 2019."
    )
    assert seed_tools.reference(introduction, "2") == output


def test_reference_to_a_marker_the_chunk_does_not_print_lists_its_markers(
    store_db: Conn, small_seed: Work, conversation: Conversation, seed_tools: SeedTools
) -> None:
    introduction = conversation.handle(chunk(store_db, small_seed, INTRODUCTION))
    recording = conversation.handle(chunk(store_db, small_seed, RECORDING))

    assert seed_tools.reference(introduction, "[7]") == (
        f"[#{introduction}] prints no Citation marker '[7]'. Its markers: [1], [2]."
    )
    assert seed_tools.reference(recording, "[1]") == f"[#{recording}] prints no Citation marker."


@pytest.mark.parametrize("reference", ["b0", "1", "[1]", "Carhart-Harris"])
def test_citing_sentences_lists_every_citing_sentence_of_a_reference(
    store_db: Conn,
    small_seed: Work,
    conversation: Conversation,
    seed_tools: SeedTools,
    reference: str,
) -> None:
    sentences = {
        c.citation.block: c.citation.citing_sentence
        for c in CitationGraphs(store_db).for_work(small_seed.id)
        if c.citation.link is not None and c.citation.link.reference_id == "b0"
    }

    output = seed_tools.citing_sentences(reference)

    introduction = chunk(store_db, small_seed, INTRODUCTION)
    discussion = chunk(store_db, small_seed, DISCUSSION)
    assert output.startswith("Reference b0 is cited 2 times in the Seed:\n\n")
    assert [(path, page, text) for _, path, page, text in shown(output)] == [
        ("1 Introduction", 1, sentences[introduction.chunk.block]),
        ("3 Discussion", 3, sentences[discussion.chunk.block]),
    ]
    assert seed_tools.chunks_read() == [introduction, discussion]


def test_citing_sentences_of_an_unknown_reference_says_so(seed_tools: SeedTools) -> None:
    assert seed_tools.citing_sentences("Nobody 1999") == (
        "No Reference of the Seed matches 'Nobody 1999'. Give its key (as `reference` shows"
        " it, e.g. b12), its printed label or words of its text."
    )


def test_a_seed_numbers_its_references_when_any_carries_a_printed_label(
    store_db: Conn, seed_tools: SeedTools, unnumbered_seed: Work
) -> None:
    by_author_and_year = SeedTools(store_db, FakeEmbedder(), Conversation(work=unnumbered_seed))

    assert seed_tools.numbered_references() is True
    assert by_author_and_year.numbered_references() is False
