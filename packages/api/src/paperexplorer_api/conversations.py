"""The API's Conversations, held in process memory until Postgres and login replace this store."""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import timedelta
from typing import Self

from paperexplorer.asking import Conversation
from paperexplorer.config import Settings

logger = logging.getLogger(__name__)


class Conversations:
    """Conversations by id, the least recently used evicted beyond `limit` or after `idle`.

    A restart clears them. An evicted Conversation is gone: asking it more is like asking an
    unknown one. Safe to use from several threads.
    """

    def __init__(
        self, *, limit: int, idle: timedelta, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._idle_s = idle.total_seconds()
        self._clock = clock
        self._lock = threading.Lock()
        # Least recently used first, each with when it was last used.
        self._held: OrderedDict[str, tuple[Conversation, float]] = OrderedDict()

    @classmethod
    def from_settings(
        cls, settings: Settings, *, clock: Callable[[], float] = time.monotonic
    ) -> Self:
        """The store `PE_CONVERSATIONS_MAX` and `PE_CONVERSATION_IDLE_MINUTES` describe."""
        return cls(
            limit=settings.conversations_max,
            idle=timedelta(minutes=settings.conversation_idle_minutes),
            clock=clock,
        )

    def add(self, conversation: Conversation) -> None:
        """Hold a new Conversation, evicting what no longer fits."""
        with self._lock:
            self._held[conversation.id] = (conversation, self._clock())
            self._evict()

    def get(self, conversation_id: str) -> Conversation | None:
        """The Conversation, now counted as used; None if unknown or evicted."""
        with self._lock:
            self._evict()
            held = self._held.get(conversation_id)
            if held is None:
                return None
            self._held[conversation_id] = (held[0], self._clock())
            self._held.move_to_end(conversation_id)
            return held[0]

    def _evict(self) -> None:
        now = self._clock()
        while self._held:
            oldest, (_, used) = next(iter(self._held.items()))
            if len(self._held) <= self._limit and now - used <= self._idle_s:
                return
            del self._held[oldest]
            logger.info("Evicted Conversation %s, last used %.0f s ago", oldest, now - used)
