"""Hand-built eval turns over the small Seed's places, for tests that need no run."""

from paperexplorer.asking import Reading
from paperexplorer.domain import Answer, AnswerSentence, ChunkId, Pointer, Support
from paperexplorer.evals import Hit, Mode, Place, PointerCheck, QuoteCheck, Turn, says_not_covered

RECORDING_PLACE = Place(heading_path=("2 Methods", "2.1 Recording"), page=2)
DISCUSSION_PLACE = Place(heading_path=("3 Discussion",), page=3)


def make_pointer(sentence: int, place: Place, support: Support, similarity: float) -> PointerCheck:
    """A Pointer of the Answer's `sentence` (index) at `place`."""
    return PointerCheck(
        sentence=sentence,
        support=support,
        similarity=similarity,
        chunk_id=ChunkId(1),
        place=place,
        matched="(the Chunk sentence)",
    )


def make_turn(
    *,
    mode: Mode = Mode.RETRIEVAL,
    repeat: int = 1,
    reading: Reading = Reading.RETRIEVAL,
    search: list[Hit] | None = None,
    pointers: list[PointerCheck] | None = None,
    quotes: list[QuoteCheck] | None = None,
    text: str = "One sentence of four words. Another one with four. A third, unpointed here.",
    error: str | None = None,
    redrafted: bool = False,
    tools: list[str] | None = None,
) -> Turn:
    """A turn whose Answer has three substantive sentences, `pointers` placed on them."""
    pointers = pointers or []
    sentences = [
        AnswerSentence(
            start=0,
            end=28,
            substantive=True,
            pointers=[
                Pointer(
                    block_id=0,
                    start=0,
                    end=1,
                    support=p.support,
                    chunk_id=p.chunk_id,
                    chunk_start=0,
                    chunk_end=1,
                    similarity=p.similarity,
                )
                for p in pointers
                if p.sentence == i
            ],
        )
        for i in range(3)
    ]
    answer = Answer(text=text, sentences=sentences, quotes=[]) if error is None else None
    tools = ["read_section"] if tools is None else tools
    return Turn(
        mode=mode,
        repeat=repeat,
        reading=reading,
        question="Q?",
        conversation_id="c1",
        trace_id="t1",
        answer=answer,
        error=error,
        search=search,
        pointers=pointers,
        quotes=quotes or [],
        says_not_covered=answer is not None and says_not_covered(answer.text),
        redrafted=redrafted,
        model_requests=2 if answer else None,
        tool_calls=len(tools),
        tools=tools,
        latency_s=12.5,
        first_delta_s=3.0,
    )
