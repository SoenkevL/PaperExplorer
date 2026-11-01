"""Readers' Choices: the Work the reader picked for a Reference, or none."""

import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import WorkId


class ReadersChoices:
    """Repository of a Work's Readers' Choices, by each Reference's printed text."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def choose(self, work: WorkId, printed: str, chosen: WorkId | None) -> None:
        """Record that the Reference printed so names `chosen`, or no Work when `None`."""
        self._conn.execute(
            "INSERT INTO readers_choices (work_id, printed, chosen_work_id) VALUES (%s, %s, %s)"
            " ON CONFLICT (work_id, printed) DO UPDATE"
            " SET chosen_work_id = EXCLUDED.chosen_work_id, chosen_at = now()",
            (work, printed, chosen),
        )

    def clear(self, work: WorkId, printed: str) -> bool:
        """Drop the reader's choice for this Reference; whether there was one."""
        return (
            self._conn.execute(
                "DELETE FROM readers_choices WHERE work_id = %s AND printed = %s", (work, printed)
            ).rowcount
            > 0
        )

    def for_work(self, work: WorkId) -> dict[str, WorkId | None]:
        """The Work's choices: each printed text with the Work chosen, `None` for none."""
        rows = self._conn.execute(
            "SELECT printed, chosen_work_id FROM readers_choices WHERE work_id = %s", (work,)
        ).fetchall()
        return {printed: None if chosen is None else WorkId(chosen) for printed, chosen in rows}
