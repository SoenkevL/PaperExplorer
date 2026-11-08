"""One Question's messages: what its requests see, and what its Conversation keeps."""

from dataclasses import replace
from typing import Self

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from paperexplorer.asking.conversation import Conversation
from paperexplorer.asking.prompts import NO_TOOLS, QUESTION_LABEL, REDRAFT


class QuestionHistory:
    """One Question on its way to joining the Conversation, which stays as it is until `commit`.

    A request sees notes that are for it alone, the model's thinking and its text as it came. The
    Conversation keeps the bare Question and the Draft as cut, and never the model's thinking.
    """

    def __init__(self, conversation: Conversation, question: str, turn: list[ModelMessage]) -> None:
        self._conversation = conversation
        self._question = question
        self._at = len(conversation.messages)  # where the Question's request is in every run's
        self._answered: list[ModelMessage] | None = None
        self.request: list[ModelMessage] = [*conversation.messages, *turn]

    @classmethod
    def whole_seed(cls, conversation: Conversation, question: str) -> Self:
        """The request closes with one turn: `NO_TOOLS`, `QUESTION_LABEL`, then the Question.

        As a turn of its own after the Question, the note was taken for the reader's message,
        and another Question answered (docs/eval-2026-10-31.md).
        """
        closing = f"{NO_TOOLS}\n\n{QUESTION_LABEL}\n{question}"
        return cls(conversation, question, [ModelRequest(parts=[UserPromptPart(closing)])])

    @classmethod
    def retrieval(cls, conversation: Conversation, question: str, found: str) -> Self:
        """The Question, then the forced search as the model's own call, and what it `found`."""
        call = ToolCallPart(
            "search", {"query": question}, tool_call_id=f"forced-{conversation.questions + 1}"
        )
        turn: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(question)]),
            ModelResponse(parts=[call]),
            ModelRequest(parts=[ToolReturnPart("search", found, tool_call_id=call.tool_call_id)]),
        ]
        return cls(conversation, question, turn)

    def answered(self, messages: list[ModelMessage], draft: str) -> None:
        """The run ended with these `messages`; its last response's text is `draft`, as cut."""
        self._answered = _with_draft(messages, draft)

    def redraft_request(self) -> list[ModelMessage]:
        """The turn without thinking, the first draft the model's response, then `REDRAFT`."""
        return [
            *_without_thinking(self._turn()),
            ModelRequest(parts=[UserPromptPart(REDRAFT)]),
        ]

    def redrafted(self, messages: list[ModelMessage], draft: str) -> None:
        """The redraft's run ended with these `messages`: its response replaces the first draft."""
        response = _with_draft(messages, draft)[-1]
        assert isinstance(response, ModelResponse), "the redraft ended without a response"
        self._answered = [*self._turn()[:-1], response]

    def commit(self) -> None:
        """The answered turn joins the Conversation, with the bare Question and no thinking."""
        turn = self._turn()
        request = turn[self._at]
        assert isinstance(request, ModelRequest), "the Question's request is not where it was put"
        parts = [
            replace(p, content=self._question) if isinstance(p, UserPromptPart) else p
            for p in request.parts
        ]
        kept = [*turn[: self._at], replace(request, parts=parts), *turn[self._at + 1 :]]
        self._conversation.messages = _without_thinking(kept)
        self._conversation.questions += 1

    def _turn(self) -> list[ModelMessage]:
        assert self._answered is not None, "the Question has not been answered"
        return self._answered


def _with_draft(messages: list[ModelMessage], draft: str) -> list[ModelMessage]:
    """The messages with the last response's text the Answer's draft, as cut, for later turns."""
    *earlier, last = messages
    if not isinstance(last, ModelResponse):
        return messages
    kept = [p for p in last.parts if not isinstance(p, TextPart)]
    return [*earlier, replace(last, parts=[*kept, TextPart(draft)])]


def _without_thinking(messages: list[ModelMessage]) -> list[ModelMessage]:
    """The messages with the model's thinking left out: it is traced, never kept or shown."""
    return [
        replace(m, parts=[p for p in m.parts if not isinstance(p, ThinkingPart)])
        if isinstance(m, ModelResponse)
        else m
        for m in messages
    ]
