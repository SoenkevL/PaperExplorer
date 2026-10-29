"""A Conversation as the answering agent holds it: the model's context and the Chunks' handles."""

import uuid

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from paperexplorer.domain import Work
from paperexplorer.store import StoredChunk


class Conversation(BaseModel):
    """The reader's Questions and the model's Answers about one Work, in order.

    Memory lives only in the model's context, so this is all a Conversation keeps: the messages
    so far (without the model's thinking) and the handle `[#n]` each Chunk shown was given,
    which it keeps for the whole Conversation. A plain object: the API holds it and passes it in.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    work: Work
    messages: list[ModelMessage] = []
    handles: dict[int, StoredChunk] = {}
    questions: int = Field(default=0, description="Questions answered so far.")

    def handle(self, chunk: StoredChunk) -> int:
        """The Chunk's handle: the one it was given when first shown, else the next number."""
        for number, known in self.handles.items():
            if known.id == chunk.id:
                return number
        number = len(self.handles) + 1
        self.handles[number] = chunk
        return number
