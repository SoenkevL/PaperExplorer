"""The Seed as the answering agent sees it: its map and the five tools that read it.

Every Chunk a tool shows carries its handle, Heading Path and page, and is remembered in the
order shown: those are the Chunks read this turn, the only ones Pointers move to and Quotes
are exact in (ADR 0008).
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

import psycopg
from psycopg.rows import TupleRow

from paperexplorer import retrieval
from paperexplorer.asking.conversation import Conversation
from paperexplorer.citations import confidence_level
from paperexplorer.domain import Scope
from paperexplorer.embedding import Embedder
from paperexplorer.parsing import Reference
from paperexplorer.store import (
    Chunks,
    CitationGraphs,
    ScoredChunk,
    StoredChunk,
    StoredCitation,
    WorkTexts,
)

logger = logging.getLogger(__name__)

# read_section shows about this many (bge-m3) tokens of a section per part.
SECTION_TOKENS = 3000
# The map shows at most this much of the text before the first heading (the abstract).
ABSTRACT_TOKENS = 1500
# A search shows at most this many Chunks, whatever the model asks for.
SEARCH_K_MAX = 20
# read_around shows at most this many neighbours on either side.
AROUND_MAX = 3

_SECTION_NUMBER = re.compile(r"§\s*(\d+)")
_BARE_NUMBER = re.compile(r"(\d+)")
_DIGITS = re.compile(r"\d+")
_MARKER_NOISE = re.compile(r"[\s\[\]()]+")


@dataclass(frozen=True, slots=True)
class Section:
    """One heading of the Seed's map: its § number, its Heading Path and the pages under it."""

    number: int
    path: tuple[str, ...]
    first_page: int
    last_page: int

    @property
    def label(self) -> str:
        """The section's § number as the map prints it, e.g. `§3`."""
        return f"§{self.number}"

    @property
    def title(self) -> str:
        """The § number with the whole Heading Path."""
        return f"{self.label} {' > '.join(self.path)}"


class SeedTools:
    """The Seed's map and the five tools for one Question, each Chunk shown with its handle."""

    def __init__(
        self, conn: psycopg.Connection[TupleRow], embedder: Embedder, conversation: Conversation
    ) -> None:
        self._conn = conn
        self._embedder = embedder
        self._conversation = conversation
        self._work = conversation.work
        self._chunks = Chunks(conn).for_work(self._work.id)
        self._sections = _sections(self._chunks)
        self._read: list[StoredChunk] = []

    def chunks_read(self) -> list[StoredChunk]:
        """Every Chunk shown this turn, once each, in the order first shown."""
        return list({c.id: c for c in self._read}.values())

    def seed_map(self) -> str:
        """The Seed's title, its heading tree with a § number per section, and its abstract."""
        lines = [self._title(), ""]
        if self._sections:
            lines.append("Its sections, for read_section (give the § number or the heading):")
            lines += [
                f"{s.label} {'  ' * (len(s.path) - 1)}{s.path[-1]} · {_pages(s)}"
                for s in self._sections
            ]
        else:
            lines.append("It has no headings: search it, or read around a handle.")
        abstract = self._abstract()
        if abstract:
            lines += ["", "Its text before the first heading, the abstract:", self._show(abstract)]
        return "\n".join(lines)

    def whole_seed(self) -> str:
        """The Seed's title and every Chunk in reading order, each with its handle: all read."""
        return f"{self._title()}\n\nIts whole text, in reading order:\n\n{self._show(self._chunks)}"

    def numbered_references(self) -> bool:
        """Whether the Seed numbers its References, so that a bare `[12]` is its own marker.

        Parsing gives a Reference its printed label only where it numbers the bibliography.
        """
        return any(r.label for r in self._references.values())

    def tokens(self) -> int:
        """The Seed's size as `whole_seed` shows it: the sum of its Chunks' (bge-m3) tokens."""
        return sum(c.chunk.tokens for c in self._chunks)

    def search(self, query: str, k: int = 8) -> str:
        """The `k` Chunks of the Seed that best match `query`, best first."""
        return self.ranked_search(query, k)[1]

    def ranked_search(self, query: str, k: int = 8) -> tuple[list[ScoredChunk], str]:
        """What `search` finds, best first, and what it shows of it."""
        k = max(1, min(k, SEARCH_K_MAX))
        found = retrieval.search(self._conn, self._embedder, query, Scope.of(self._work.id), k=k)
        if not found:
            return [], f"Nothing in the Seed matches {query!r}."
        return found, self._show(found)

    def read_section(self, heading: str, part: int = 1) -> str:
        """The Chunks under one heading in reading order, about `SECTION_TOKENS` per part."""
        section = self._section(heading)
        if section is None:
            return (
                f"The Seed has no section {heading!r}. Give a § number or a heading from the map."
            )
        under = [
            c
            for c in self._chunks
            if tuple(c.chunk.heading_path[: len(section.path)]) == section.path
        ]
        parts = _parts(under, SECTION_TOKENS)
        if not 1 <= part <= len(parts):
            return f"{section.title} has {len(parts)} part{'s' if len(parts) != 1 else ''}."
        output = f"{section.title} · {_pages(section)}, part {part} of {len(parts)}\n\n"
        output += self._show(parts[part - 1])
        if part < len(parts):
            output += f'\n\nThe section goes on: read_section("{section.label}", part={part + 1}).'
        return output

    def read_around(self, handle: int | str, n: int = 1) -> str:
        """The Chunk behind `handle` with `n` neighbours on either side, in reading order."""
        chunk = self._chunk(handle)
        if chunk is None:
            return _unknown_handle(handle)
        n = max(1, min(n, AROUND_MAX))
        i = next(i for i, c in enumerate(self._chunks) if c.id == chunk.id)
        return self._show(self._chunks[max(0, i - n) : i + n + 1])

    def reference(self, handle: int | str, marker: str) -> str:
        """The Reference printed behind a Citation marker in a Chunk, and its link's confidence."""
        chunk = self._chunk(handle)
        if chunk is None:
            return _unknown_handle(handle)
        shown = f"[#{self._conversation.handle(chunk)}]"
        span = chunk.chunk.span
        printed = [
            c
            for c in self._citations
            if c.citation.block == chunk.chunk.block
            and span.start <= c.citation.span.start < span.end
        ]
        if not printed:
            return f"{shown} prints no Citation marker."
        matched = self._matching(printed, marker)
        if not matched:
            markers = ", ".join(dict.fromkeys(c.citation.text for c in printed))
            return f"{shown} prints no Citation marker {marker!r}. Its markers: {markers}."
        answers: list[str] = []
        for c in matched:
            link = c.citation.link
            if link is None:
                answers.append(
                    f"{c.citation.text} in {shown} is an Unlinked Citation:"
                    " no evidence ties it to a Reference."
                )
                continue
            level = confidence_level(link).value
            answers.append(
                f"{c.citation.text} in {shown} points at Reference {link.reference_id}"
                f" (Citation Link confidence {link.confidence:.2f}, {level}), as printed:\n"
                f"{self._references[link.reference_id].text}"
            )
        return "\n\n".join(dict.fromkeys(answers))

    def citing_sentences(self, reference: str) -> str:
        """Every Citing Sentence of one Reference in the Seed, each with its Chunk's handle."""
        candidates = self._find_references(reference)
        if not candidates:
            return (
                f"No Reference of the Seed matches {reference!r}. Give its key (as `reference`"
                " shows it, e.g. b12), its printed label or words of its text."
            )
        if len(candidates) > 1:
            listed = "; ".join(f"{r.id}: {r.text[:80]}" for r in candidates[:8])
            return f"Several References match {reference!r}; give one key. {listed}"
        found = candidates[0]
        sentences: dict[tuple[int, str], StoredChunk] = {}
        for c in self._citations:
            link = c.citation.link
            if link is None or link.reference_id != found.id:
                continue
            chunk = self._chunk_of(c)
            if chunk is None:
                logger.debug("Citation %s of %s sits in no Chunk", c.id, found.id)
                continue
            sentences.setdefault((chunk.id, c.citation.citing_sentence), chunk)
        if not sentences:
            return f"The Seed's body never cites Reference {found.id}."
        shown: list[str] = []
        for (_, sentence), chunk in sentences.items():
            self._read.append(chunk)
            shown.append(f"{self._header(chunk)}\n{sentence}")
        times = "time" if len(shown) == 1 else "times"
        return f"Reference {found.id} is cited {len(shown)} {times} in the Seed:\n\n" + "\n\n".join(
            shown
        )

    def _title(self) -> str:
        work = self._work
        byline = ", ".join(
            part
            for part in (_creators(work.creators), str(work.year) if work.year else None)
            if part
        )
        title = work.title or work.zotero_key or f"Work {work.id}"
        return f"The Seed: {title} ({byline})" if byline else f"The Seed: {title}"

    def _show(self, chunks: Sequence[StoredChunk]) -> str:
        self._read += chunks
        return "\n\n".join(f"{self._header(c)}\n{c.chunk.text}" for c in chunks)

    def _header(self, chunk: StoredChunk) -> str:
        heading = " > ".join(chunk.chunk.heading_path) or "(no heading)"
        return f"[#{self._conversation.handle(chunk)}] {heading} · p. {chunk.chunk.page}"

    def _abstract(self) -> list[StoredChunk]:
        """The Chunks before the first heading, else those under a first heading "Abstract"."""
        lead = next(
            (i for i, c in enumerate(self._chunks) if c.chunk.heading_path), len(self._chunks)
        )
        chunks = self._chunks[:lead]
        if (
            not chunks
            and self._chunks
            and self._chunks[0].chunk.heading_path[0].casefold()
            in {
                "abstract",
                "summary",
            }
        ):
            top = self._chunks[0].chunk.heading_path[0]
            chunks = [c for c in self._chunks if c.chunk.heading_path[:1] == [top]]
        return _parts(chunks, ABSTRACT_TOKENS)[0] if chunks else []

    def _section(self, heading: str) -> Section | None:
        text = heading.strip()
        number = _SECTION_NUMBER.match(text) or _BARE_NUMBER.fullmatch(text)
        if number:
            return next((s for s in self._sections if s.number == int(number.group(1))), None)
        want = text.casefold()
        for exact in (
            [s for s in self._sections if " > ".join(s.path).casefold() == want],
            [s for s in self._sections if s.path[-1].casefold() == want],
            [s for s in self._sections if want in s.path[-1].casefold()],
        ):
            if len(exact) == 1:
                return exact[0]
        return None

    def _chunk(self, handle: int | str) -> StoredChunk | None:
        number = _handle_number(handle)
        return self._conversation.handles.get(number) if number is not None else None

    def _chunk_of(self, citation: StoredCitation) -> StoredChunk | None:
        c = citation.citation
        return next(
            (
                s
                for s in self._chunks
                if s.chunk.block == c.block
                and s.chunk.span.start <= c.span.start < s.chunk.span.end
            ),
            None,
        )

    def _matching(self, printed: list[StoredCitation], marker: str) -> list[StoredCitation]:
        """The Citations whose marker is `marker`, or whose Reference has it as its label."""
        want = _marker(marker)
        exact = [
            c
            for c in printed
            if _marker(c.citation.text) == want
            or (
                c.citation.link is not None
                and _marker(self._references[c.citation.link.reference_id].label or "") == want
            )
        ]
        if exact or len(want) < 3:
            return exact
        return [
            c
            for c in printed
            if want in _marker(c.citation.text)
            or (len(_marker(c.citation.text)) >= 3 and _marker(c.citation.text) in want)
        ]

    def _find_references(self, reference: str) -> list[Reference]:
        text = reference.strip()
        references = list(self._references.values())
        for found in (
            [r for r in references if r.id == text],
            [r for r in references if r.label and _marker(r.label) == _marker(text)],
        ):
            if found:
                return found
        if len(text) < 3:
            return []
        return [r for r in references if text.casefold() in r.text.casefold()]

    @cached_property
    def _citations(self) -> list[StoredCitation]:
        return CitationGraphs(self._conn).for_work(self._work.id)

    @cached_property
    def _references(self) -> dict[str, Reference]:
        parsed = WorkTexts(self._conn).get(self._work.id)
        return {r.id: r for r in parsed.references} if parsed else {}


def _sections(chunks: Sequence[StoredChunk]) -> list[Section]:
    """Every Heading Path prefix the Chunks sit under, numbered in reading order."""
    pages: dict[tuple[str, ...], list[int]] = {}
    for c in chunks:
        path = c.chunk.heading_path
        for depth in range(1, len(path) + 1):
            pages.setdefault(tuple(path[:depth]), []).append(c.chunk.page)
    return [
        Section(number=i, path=path, first_page=min(p), last_page=max(p))
        for i, (path, p) in enumerate(pages.items(), start=1)
    ]


def _parts(chunks: Sequence[StoredChunk], budget: int) -> list[list[StoredChunk]]:
    """The Chunks in consecutive parts of at most `budget` tokens, each at least one Chunk."""
    parts: list[list[StoredChunk]] = []
    used = 0
    for c in chunks:
        if not parts or (parts[-1] and used + c.chunk.tokens > budget):
            parts.append([])
            used = 0
        parts[-1].append(c)
        used += c.chunk.tokens
    return parts


def _pages(section: Section) -> str:
    if section.first_page == section.last_page:
        return f"p. {section.first_page}"
    return f"pp. {section.first_page}\u2013{section.last_page}"  # an en dash, as printed


def _creators(creators: Sequence[str]) -> str | None:
    if not creators:
        return None
    return ", ".join(creators) if len(creators) <= 3 else f"{creators[0]} et al."


def _handle_number(handle: int | str) -> int | None:
    if isinstance(handle, int):
        return handle
    digits = _DIGITS.search(handle)
    return int(digits.group(0)) if digits else None


def _unknown_handle(handle: int | str) -> str:
    number = _handle_number(handle)
    shown = f"[#{number}]" if number is not None else repr(handle)
    return f"No Chunk has the handle {shown}. Use a handle a tool has shown you."


def _marker(text: str) -> str:
    """A Citation marker for comparison: case-folded, without spaces, brackets and end commas."""
    return _MARKER_NOISE.sub("", text.casefold()).strip(";,.")
