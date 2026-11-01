"""Relay a Question's events from the API to the browser, as server-sent events carrying HTML.

The browser's EventSource can only GET, and the API takes a Question by POST. So the sidebar
posts its Question to the web app, which holds it (`PendingQuestions`) until the page's
EventSource GETs its stream; `relay` then asks the API and re-sends each event as HTML.
"""

import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Generator, Sequence
from contextlib import closing
from itertools import chain

import jinja2
from pydantic import BaseModel, JsonValue

from paperexplorer_api.client import ApiClient, ConflictError, NotFoundError
from paperexplorer_api.schemas import (
    Answer,
    AnsweredEvent,
    DeltaEvent,
    DraftPiece,
    ErrorEvent,
    RedraftEvent,
    StepEvent,
)
from paperexplorer_web.answers import answer_runs

logger = logging.getLogger(__name__)

# Questions posted but never streamed (a page closed at once) are forgotten beyond this many.
PENDING_MAX = 64
LOST = (
    "This Question is no longer waiting for its Answer (the page lost its stream, or the web"
    " app restarted); ask it again."
)
STILL_ANSWERING = (
    "The Conversation is still answering the Question before this one; wait for its Answer,"
    " then ask again."
)
ENDED = "The API's stream ended without an Answer; ask again."


class PendingQuestion(BaseModel):
    """A Question the sidebar posted, waiting for the page's EventSource to fetch its Answer."""

    work_id: int
    conversation_id: str
    text: str


class PendingQuestions:
    """Questions posted and not yet streamed, by id, in the web app's memory.

    Each is taken once, so a reconnecting EventSource does not ask its Question twice.
    """

    def __init__(self, capacity: int = PENDING_MAX) -> None:
        self._capacity = capacity
        self._held: OrderedDict[str, PendingQuestion] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, question: PendingQuestion) -> str:
        """Hold the Question until its stream takes it; its id names that stream."""
        question_id = uuid.uuid4().hex
        with self._lock:
            self._held[question_id] = question
            while len(self._held) > self._capacity:
                self._held.popitem(last=False)
        return question_id

    def take(self, question_id: str) -> PendingQuestion | None:
        """The Question, the first time; None after that, or for an id never given."""
        with self._lock:
            return self._held.pop(question_id, None)


class Views:
    """The HTML the relay sends, rendered from the sidebar's templates (autoescaped)."""

    def __init__(self, env: jinja2.Environment) -> None:
        self._env = env

    def step(self, event: StepEvent) -> str:
        """A line naming the tool the model runs, and its arguments."""
        return self._render("step", tool=event.tool, args=_arguments(event.args))

    def draft(self, pieces: Sequence[DraftPiece]) -> str:
        """More of the Draft: its text, and each handle as a placeholder chip."""
        return self._render("draft", pieces=pieces)

    def redraft(self, question_id: str) -> str:
        """The draft emptied, out of band, and the line saying Pointers are being added."""
        return self._render("redraft", question_id=question_id)

    def answer(self, answer: Answer, steps: Sequence[StepEvent]) -> str:
        """The resolved Answer with its Pointer chips and Quotes, replacing the reply so far."""
        return self._render("answer", runs=answer_runs(answer), steps=_lines(steps))

    def error(self, message: str, steps: Sequence[StepEvent] = ()) -> str:
        """The turn's failure, replacing the reply so far."""
        return self._render("error", message=message, steps=_lines(steps))

    def restarted(self, question_id: str, conversation_id: str) -> str:
        """The notice that a new Conversation took over, and its id for the page's form."""
        return self._render("restarted", question_id=question_id, conversation_id=conversation_id)

    def _render(self, name: str, **context: object) -> str:
        return self._env.get_template(f"conversation/{name}.html").render(context)


def relay(
    api: ApiClient, question_id: str, question: PendingQuestion | None, views: Views
) -> Generator[str]:
    """The Question's events as server-sent events carrying HTML, ending in `answer` or `error`.

    Events: `step`, `delta` (Draft pieces to append, handles as chips), `redraft` (the draft
    is written again with its handles: emptied, and a line says so), `conversation` (the
    Conversation was gone and a new one took over), then `answer` or `error`, which replace the
    reply and so close the page's EventSource. Closing this iterator closes the API's stream,
    cancelling the turn.
    """
    if question is None:
        yield sse("error", views.error(LOST))
        return
    try:
        yield from _relay(api, question_id, question, views)
    except Exception as e:
        # Whatever broke, the page must hear an end, or its EventSource asks again.
        logger.warning("The stream of Question %s broke off", question_id, exc_info=True)
        yield sse("error", views.error(f"The Answer's stream broke off ({e}); ask again."))


def _relay(
    api: ApiClient, question_id: str, question: PendingQuestion, views: Views
) -> Generator[str]:
    events = api.ask(question.conversation_id, question.text)
    try:
        first = next(events, None)
    except NotFoundError:
        try:
            conversation = api.start_conversation(question.work_id)
        except ConflictError as e:
            yield sse("error", views.error(not_embedded(e)))
            return
        logger.info(
            "Conversation %s is gone; Question %s starts Conversation %s",
            question.conversation_id,
            question_id,
            conversation.id,
        )
        yield sse("conversation", views.restarted(question_id, conversation.id))
        events = api.ask(conversation.id, question.text)
        first = next(events, None)
    except ConflictError:
        yield sse("error", views.error(STILL_ANSWERING))
        return
    steps: list[StepEvent] = []
    with closing(events):
        for event in chain(() if first is None else (first,), events):
            match event:
                case StepEvent():
                    steps.append(event)
                    yield sse("step", views.step(event))
                case DeltaEvent(pieces=pieces):
                    yield sse("delta", views.draft(pieces))
                case RedraftEvent():
                    yield sse("redraft", views.redraft(question_id))
                case AnsweredEvent(answer=answer):
                    yield sse("answer", views.answer(answer, steps))
                    return
                case ErrorEvent(message=message):
                    yield sse("error", views.error(message, steps))
                    return
    yield sse("error", views.error(ENDED, steps))


def not_embedded(error: ConflictError) -> str:
    """What the sidebar says when the API cannot start a Conversation about the Work."""
    return f"Not asked: this Work cannot be asked until its Chunks are embedded ({error})."


def sse(event: str, html: str) -> str:
    """One server-sent event named `event` carrying `html`, one data line per line of it."""
    lines = re.split(r"\r\n|\r|\n", html)
    return f"event: {event}\n" + "".join(f"data: {line}\n" for line in lines) + "\n"


def _arguments(args: dict[str, JsonValue]) -> str:
    return ", ".join(v if isinstance(v, str) else json.dumps(v) for v in args.values())


def _lines(steps: Sequence[StepEvent]) -> list[tuple[str, str]]:
    return [(s.tool, _arguments(s.args)) for s in steps]
