"""What `ask` streams while it answers a Question: steps, the draft, then an Answer or an error."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from paperexplorer.asking.draft import DraftPiece
from paperexplorer.asking.reading import Reading
from paperexplorer.domain import Answer


class StepEvent(BaseModel):
    """The model runs a tool (the forced first search too): its name and arguments."""

    type: Literal["step"] = "step"
    tool: str
    args: dict[str, JsonValue]


class DeltaEvent(BaseModel):
    """More of the model's draft, cut into text and handles (`DraftCut`); never its thinking."""

    type: Literal["delta"] = "delta"
    pieces: list[DraftPiece]


class RedraftEvent(BaseModel):
    """The draft gave no handle, so the model is asked once more for its Answer, with them.

    The draft streamed so far is dropped: the deltas that follow are the new draft's.
    """

    type: Literal["redraft"] = "redraft"


class AnsweredEvent(BaseModel):
    """The resolved Answer, with its Pointers and Quotes; the turn's last event.

    `trace_id` is the Question's `question` trace (None with tracing off), for scores added later;
    `reading` is how the turn read the Seed, whole or by retrieval; `redrafted` says the Answer is
    the draft written again with handles (a redraft that failed leaves the first standing).
    """

    type: Literal["answer"] = "answer"
    answer: Answer
    trace_id: str | None = None
    reading: Reading
    redrafted: bool = False


class ErrorEvent(BaseModel):
    """The turn failed and ends here; the Conversation stays as it was before the Question.

    `reading` is how the turn read the Seed, None when it failed before it chose.
    """

    type: Literal["error"] = "error"
    message: str
    trace_id: str | None = None
    reading: Reading | None = None


type AnswerEvent = Annotated[
    StepEvent | DeltaEvent | RedraftEvent | AnsweredEvent | ErrorEvent, Field(discriminator="type")
]
"""One event of a turn, told apart by `type`: what the API sends as one SSE event."""
