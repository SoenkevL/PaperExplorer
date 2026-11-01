"""Resolutions: each Reference of a Work matched to a Work, or Unchecked, or Unresolved."""

import psycopg
from psycopg.rows import TupleRow, class_row

from paperexplorer.domain import Resolution, WorkId


class Resolutions:
    """Repository of a Work's Resolutions; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def replace(self, work: WorkId, resolutions: list[Resolution]) -> None:
        """Store these as the Work's Resolutions, dropping the earlier ones."""
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM resolutions WHERE work_id = %s", (work,))
            cur.executemany(
                "INSERT INTO resolutions (work_id, reference_key, state, resolved_work_id, level,"
                " confidence, evidence, layer, doi_conflict_work_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        work, r.reference, r.state.value, r.work,
                        r.level.value if r.level else None, r.confidence, r.evidence,
                        r.layer.value if r.layer else None, r.doi_conflict,
                    )
                    for r in resolutions
                ],
            )  # fmt: skip

    def for_work(self, work: WorkId) -> list[Resolution]:
        """The Work's Resolutions, in the order its References are printed."""
        with self._conn.cursor(row_factory=class_row(Resolution)) as cur:
            return cur.execute(
                "SELECT r.reference_key AS reference, r.state, r.resolved_work_id AS work,"
                " r.level, r.confidence, r.evidence, r.layer,"
                " r.doi_conflict_work_id AS doi_conflict"
                " FROM resolutions r JOIN work_references w"
                " ON w.work_id = r.work_id AND w.key = r.reference_key"
                " WHERE r.work_id = %s ORDER BY w.position",
                (work,),
            ).fetchall()
