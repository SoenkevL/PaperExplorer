"""What `ask` streams while it answers a Question: steps, draft text, then an Answer or an error."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from paperexplorer.domain import Answer


class StepEvent(BaseModel):
    """The model runs a tool (the forced first search too): its name and arguments."""

    type: Literal["step"] = "step"
    tool: str
    args: dict[str, JsonValue]


class DeltaEvent(BaseModel):
    """More of the model's draft, handles and all; never its thinking."""

    type: Literal["delta"] = "delta"
    text: str


class AnsweredEvent(BaseModel):
    """The resolved Answer, with its Pointers and Quotes; the turn's last event.

    `trace_id` is the Question's `question` trace (None with tracing off), for scores added later.
    """

    type: Literal["answer"] = "answer"
    answer: Answer
    trace_id: str | None = None


class ErrorEvent(BaseModel):
    """The turn failed and ends here; the Conversation stays as it was before the Question."""

    type: Literal["error"] = "error"
    message: str
    trace_id: str | None = None


type AnswerEvent = Annotated[
    StepEvent | DeltaEvent | AnsweredEvent | ErrorEvent, Field(discriminator="type")
]
"""One event of a turn, told apart by `type`: what the API sends as one SSE event."""
