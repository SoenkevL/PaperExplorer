"""Per-Work Stage status: whether each Stage last ran, finished or failed."""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

import psycopg
from psycopg.rows import TupleRow, class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from paperexplorer.domain import WorkId


class StageState(StrEnum):
    """Where a Stage stands for a Work; a Stage that never ran has no status (pending)."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StageStatus(BaseModel):
    """The last run of one Stage for one Work."""

    stage: str
    state: StageState
    started_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]
    error: str | None


class StageStatuses:
    """Repository of Stage status per Work; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def start(self, work: WorkId, stage: str) -> None:
        """Record that the Stage is running for the Work, forgetting its previous run."""
        self._conn.execute(
            "INSERT INTO stage_status (work_id, stage, state, started_at)"
            " VALUES (%s, %s, 'running', clock_timestamp())"
            " ON CONFLICT (work_id, stage) DO UPDATE SET state = 'running',"
            " started_at = EXCLUDED.started_at, finished_at = NULL, counts = '{}', error = NULL",
            (work, stage),
        )

    def done(self, work: WorkId, stage: str, counts: dict[str, int]) -> None:
        """Record that the running Stage finished, with what it counted."""
        self._conn.execute(
            "UPDATE stage_status SET state = 'done', finished_at = clock_timestamp(), counts = %s"
            " WHERE work_id = %s AND stage = %s",
            (Jsonb(counts), work, stage),
        )

    def fail(self, work: WorkId, stage: str, error: str) -> None:
        """Record that the running Stage failed, and why."""
        self._conn.execute(
            "UPDATE stage_status SET state = 'failed', finished_at = clock_timestamp(), error = %s"
            " WHERE work_id = %s AND stage = %s",
            (error, work, stage),
        )

    def clear(self, work: WorkId, stages: Sequence[str]) -> None:
        """Make the Stages pending again for the Work, e.g. after what they read has changed."""
        self._conn.execute(
            "DELETE FROM stage_status WHERE work_id = %s AND stage = ANY(%s)", (work, list(stages))
        )

    def for_work(self, work: WorkId) -> dict[str, StageStatus]:
        """Status of every Stage that has run for the Work, by Stage name, oldest run first."""
        with self._conn.cursor(row_factory=class_row(StageStatus)) as cur:
            rows = cur.execute(
                "SELECT stage, state, started_at, finished_at, counts, error FROM stage_status"
                " WHERE work_id = %s ORDER BY started_at",
                (work,),
            ).fetchall()
        return {s.stage: s for s in rows}
