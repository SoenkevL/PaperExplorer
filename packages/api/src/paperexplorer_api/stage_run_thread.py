"""The Stage Run thread: what the reader started runs one at a time, whoever is still listening."""

import logging
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.rows import TupleRow

from paperexplorer import pipeline
from paperexplorer.domain import WorkId
from paperexplorer.pipeline import StageContext
from paperexplorer.store import StageStatuses
from paperexplorer_api.routes import Connections
from paperexplorer_api.schemas import TEXT_STAGES

logger = logging.getLogger(__name__)

type StageContexts = Callable[[psycopg.Connection[TupleRow]], StageContext]
"""A Stage Run's `StageContext` around the store connection it opened for itself."""

type Run = Literal["queued", "running"]

# A shutdown does not wait for a long Stage: it rolls back, and the next start says so.
_STOP_WAIT_S = 2.0


@dataclass(frozen=True)
class _Queued:
    work: WorkId
    stages: Sequence[str]


class StageRunThread:
    """Runs Stage Runs in the order started, one at a time and past their requests' ends."""

    def __init__(
        self,
        connections: Connections,
        contexts: StageContexts,
        *,
        forget_work: Callable[[WorkId], None] | None = None,
    ) -> None:
        self._connections = connections
        self._contexts = contexts
        self._forget_work = forget_work
        self._changed = threading.Condition()
        self._queue: deque[_Queued] = deque()
        self._running: WorkId | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    def run_of(self, work: WorkId) -> Run | None:
        """Whether the Work waits its turn, is running, or neither."""
        with self._changed:
            if self._running == work:
                return "running"
            return "queued" if any(q.work == work for q in self._queue) else None

    def submit(self, work: WorkId, stages: Sequence[str]) -> bool:
        """Queue a run of the Stages; False, and nothing queued, if the Work already has a run."""
        with self._changed:
            if self._running == work or any(q.work == work for q in self._queue):
                return False
            self._queue.append(_Queued(work, stages))
            if self._thread is None:
                self._stopping = False
                self._thread = threading.Thread(target=self._serve, name="ingesting", daemon=True)
                self._thread.start()
            self._changed.notify_all()
            return True

    def cancel(self, work: WorkId) -> bool:
        """Take the Work off the queue; False if it is not waiting."""
        with self._changed:
            for queued in self._queue:
                if queued.work == work:
                    self._queue.remove(queued)
                    return True
            return False

    def stop(self) -> None:
        """Drop what waits and end the thread, without waiting long for a running Stage."""
        with self._changed:
            thread, self._thread = self._thread, None
            self._queue.clear()
            self._stopping = True
            self._changed.notify_all()
        if thread is not None:
            thread.join(_STOP_WAIT_S)

    def _serve(self) -> None:
        while True:
            with self._changed:
                while not self._queue and not self._stopping:
                    self._changed.wait()
                if self._stopping:
                    return
                queued = self._queue.popleft()
                self._running = queued.work
            try:
                self._run(queued)
            finally:
                with self._changed:
                    self._running = None

    def _run(self, queued: _Queued) -> None:
        try:
            with self._connections() as conn:
                ctx = self._contexts(conn)
                try:
                    pipeline.run_chain(
                        ctx,
                        queued.stages,
                        queued.work,
                        after=lambda name: self._after(queued, name),
                    )
                finally:
                    ctx.tracing.flush()
        except Exception as e:
            logger.warning("The Stage Run of Work %d ended early: %s", queued.work, e)
            self._record_failure(queued.work, e)

    def _after(self, queued: _Queued, stage: str) -> None:
        """Forget Conversations as the Chunks they hold go, not when a long `embed` has ended."""
        if self._forget_work is not None and stage in TEXT_STAGES:
            self._forget_work(queued.work)

    def _record_failure(self, work: WorkId, error: Exception) -> None:
        """Say why on a connection of its own: `run_stage` cannot once the store has gone away."""
        try:
            with self._connections() as conn:
                StageStatuses(conn).fail_running(work, str(error) or type(error).__name__)
        except psycopg.Error as e:
            logger.warning("Could not record why Work %d stopped: %s", work, e)
