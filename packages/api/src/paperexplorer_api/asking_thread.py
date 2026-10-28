"""The asking thread: every Question's turn runs on an event loop of its own, beside the API's."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import suppress

import psycopg
from psycopg.rows import TupleRow

from paperexplorer import asking
from paperexplorer_api.routes import Connections

logger = logging.getLogger(__name__)

type AskingContexts = Callable[[psycopg.Connection[TupleRow]], asking.AskingContext]
"""A turn's `AskingContext` around the store connection the turn opened for itself."""


class AskingThread:
    """Runs each Question's turn on one thread and event loop of its own, beside the API's.

    The agent's tools call the store and the Embedder synchronously, blocking their loop for as
    long as a call takes (seconds while Ollama loads a model), so turns stay off the API's loop.
    Every turn shares the one loop, since the chat model's HTTP client keeps its connections on
    the loop that opened them, and opens a store connection of its own. A Conversation answers
    one Question at a time. The thread starts with the first Question.
    """

    def __init__(self, connections: Connections, contexts: AskingContexts) -> None:
        self._connections = connections
        self._contexts = contexts
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._answering: set[str] = set()

    def answering(self, conversation_id: str) -> bool:
        """Whether a turn of the Conversation is running."""
        with self._lock:
            return conversation_id in self._answering

    async def ask(
        self, conversation: asking.Conversation, question: str
    ) -> AsyncIterator[asking.AnswerEvent]:
        """`asking.ask` on the asking thread: the turn's events, relayed to the caller's loop.

        A Conversation still answering gets an `ErrorEvent`. Closing the stream cancels the turn;
        the Conversation takes its next Question once the cancelled turn has ended.
        """
        with self._lock:
            busy = conversation.id in self._answering
            self._answering.add(conversation.id)
            loop = self._started()
        if busy:
            yield asking.ErrorEvent(message=still_answering(conversation.id))
            return
        here = asyncio.get_running_loop()
        events: asyncio.Queue[asking.AnswerEvent | None] = asyncio.Queue()

        def relay(event: asking.AnswerEvent | None) -> None:
            # Once the caller's loop has closed, nobody listens any more.
            with suppress(RuntimeError):
                here.call_soon_threadsafe(events.put_nowait, event)

        turn = asyncio.run_coroutine_threadsafe(self._turn(conversation, question, relay), loop)
        try:
            while (event := await events.get()) is not None:
                yield event
        finally:
            turn.cancel()

    def stop(self) -> None:
        """Cancel every running turn and end the thread; the next Question starts it again."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or thread is None:
            return
        asyncio.run_coroutine_threadsafe(_cancel_turns(), loop).result()
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    def _started(self) -> asyncio.AbstractEventLoop:
        """The asking loop, started now if it is not running; call with the lock held."""
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="asking", daemon=True)
            thread.start()
            self._loop, self._thread = loop, thread
        return self._loop

    async def _turn(
        self,
        conversation: asking.Conversation,
        question: str,
        relay: Callable[[asking.AnswerEvent | None], None],
    ) -> None:
        """One turn on the asking loop, with a store connection of its own."""
        try:
            with self._connections() as conn:
                async for event in asking.ask(self._contexts(conn), conversation, question):
                    relay(event)
        except Exception as error:
            logger.exception("The turn of Conversation %s failed", conversation.id)
            relay(asking.ErrorEvent(message=f"The Question could not be answered: {error}"))
        finally:
            with self._lock:
                self._answering.discard(conversation.id)
            relay(None)


def still_answering(conversation_id: str) -> str:
    """Why a Conversation takes no Question now."""
    return f"Conversation {conversation_id} is still answering a Question; ask once it has"


async def _cancel_turns() -> None:
    turns = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for turn in turns:
        turn.cancel()
    await asyncio.gather(*turns, return_exceptions=True)
