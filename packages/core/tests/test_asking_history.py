"""What one Question's requests see and what its Conversation keeps: no model, no database."""

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

from paperexplorer.asking import Conversation
from paperexplorer.asking.history import QuestionHistory
from paperexplorer.asking.prompts import NO_TOOLS, QUESTION_LABEL, REDRAFT
from paperexplorer.domain import Work, WorkId

QUESTION = "What did the authors measure?"
CLOSING = f"{NO_TOOLS}\n\n{QUESTION_LABEL}\n{QUESTION}"


def _conversation() -> Conversation:
    return Conversation(
        work=Work(
            id=WorkId(1),
            zotero_key=None,
            title="The Seed",
            creators=[],
            year=None,
            doi=None,
            pdf=None,
            synced_at=None,
            text_origin=None,
        )
    )


def _asked(question: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(question)])


def _said(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def _thought(text: str) -> ModelResponse:
    return ModelResponse(parts=[ThinkingPart("Let me think."), TextPart(text)])


def _texts(messages: list[ModelMessage]) -> list[list[str]]:
    """Every response's parts, by what they say: thinking shows, and a request shows nothing."""
    return [
        [p.content for p in m.parts if isinstance(p, TextPart | ThinkingPart)]
        for m in messages
        if isinstance(m, ModelResponse)
    ]


def _prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]


def test_reading_the_whole_seed_the_request_closes_with_the_note_and_the_question() -> None:
    conversation = _conversation()
    conversation.messages = [_asked("Earlier?"), _said("Earlier.")]

    history = QuestionHistory.whole_seed(conversation, QUESTION)

    assert _prompts(history.request) == ["Earlier?", CLOSING]


def test_reading_by_retrieval_the_request_holds_the_question_and_the_search_as_a_call() -> None:
    conversation = _conversation()
    conversation.questions = 2

    history = QuestionHistory.retrieval(conversation, QUESTION, "[#1] Found.")

    question, call, result = history.request
    assert _prompts([question]) == [QUESTION]
    assert isinstance(call, ModelResponse)
    [part] = call.parts
    assert isinstance(part, ToolCallPart)
    assert (part.tool_name, part.args) == ("search", {"query": QUESTION})
    assert part.tool_call_id == "forced-3"
    assert isinstance(result, ModelRequest)
    [returned] = result.parts
    assert isinstance(returned, ToolReturnPart)
    assert (returned.content, returned.tool_call_id) == ("[#1] Found.", "forced-3")


def test_the_conversation_is_untouched_until_the_turn_is_committed() -> None:
    conversation = _conversation()
    history = QuestionHistory.whole_seed(conversation, QUESTION)

    history.answered([*history.request, _said("Entropy. [#1]")], "Entropy. [#1]")

    assert conversation.messages == []
    assert conversation.questions == 0


def test_committed_the_conversation_keeps_the_bare_question_and_the_draft_not_the_note() -> None:
    conversation = _conversation()
    history = QuestionHistory.whole_seed(conversation, QUESTION)

    history.answered([*history.request, _said("Entropy. [#1]")], "Entropy. [#1]")
    history.commit()

    assert _prompts(conversation.messages) == [QUESTION]
    assert _texts(conversation.messages) == [["Entropy. [#1]"]]
    assert conversation.questions == 1


def test_a_later_question_rewrites_only_its_own_request() -> None:
    conversation = _conversation()
    first = QuestionHistory.whole_seed(conversation, "Earlier?")
    first.answered([*first.request, _said("Earlier.")], "Earlier.")
    first.commit()

    second = QuestionHistory.whole_seed(conversation, QUESTION)

    assert _prompts(second.request) == ["Earlier?", CLOSING]
    second.answered([*second.request, _said("Later.")], "Later.")
    second.commit()
    assert _prompts(conversation.messages) == ["Earlier?", QUESTION]
    assert conversation.questions == 2


def test_by_retrieval_a_later_question_keeps_every_search_and_its_own_call_id() -> None:
    conversation = _conversation()
    first = QuestionHistory.retrieval(conversation, "Earlier?", "[#1] Found.")
    first.answered([*first.request, _said("Earlier.")], "Earlier.")
    first.commit()

    second = QuestionHistory.retrieval(conversation, QUESTION, "[#2] Found.")
    second.answered([*second.request, _said("Later.")], "Later.")
    second.commit()

    assert _prompts(conversation.messages) == ["Earlier?", QUESTION]
    assert _texts(conversation.messages) == [[], ["Earlier."], [], ["Later."]]
    calls = [
        p.tool_call_id
        for m in conversation.messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    assert calls == ["forced-1", "forced-2"]
    assert conversation.questions == 2


def test_the_conversation_keeps_the_draft_as_cut_and_no_thinking() -> None:
    conversation = _conversation()
    history = QuestionHistory.retrieval(conversation, QUESTION, "[#1] Found.")

    history.answered([*history.request, _thought("Entropy. <|channel>leak")], "Entropy.")
    history.commit()

    assert _texts(conversation.messages[-1:]) == [["Entropy."]]


def test_the_redraft_request_is_the_turn_without_thinking_then_the_note() -> None:
    conversation = _conversation()
    history = QuestionHistory.whole_seed(conversation, QUESTION)
    history.answered([*history.request, _thought("Entropy.")], "Entropy.")

    request = history.redraft_request()

    assert _texts(request) == [["Entropy."]]
    assert _prompts(request) == [CLOSING, REDRAFT]
    assert isinstance(request[-1], ModelRequest)


def test_a_redraft_replaces_only_the_last_response_and_its_note_is_not_kept() -> None:
    conversation = _conversation()
    history = QuestionHistory.whole_seed(conversation, QUESTION)
    history.answered([*history.request, _said("Entropy.")], "Entropy.")
    request = history.redraft_request()

    history.redrafted([*request, _thought("Entropy. [#1] <|end>")], "Entropy. [#1]")
    history.commit()

    assert _prompts(conversation.messages) == [QUESTION]
    assert _texts(conversation.messages) == [["Entropy. [#1]"]]


def test_without_a_redraft_the_first_draft_is_kept() -> None:
    conversation = _conversation()
    history = QuestionHistory.whole_seed(conversation, QUESTION)
    history.answered([*history.request, _said("Entropy.")], "Entropy.")
    history.redraft_request()

    history.commit()

    assert _texts(conversation.messages) == [["Entropy."]]
