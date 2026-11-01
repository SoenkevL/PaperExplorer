"""Registry responses: what doi.org, Crossref and OpenAlex answered, per source and key."""

from collections.abc import Sequence
from datetime import timedelta

import psycopg
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel, JsonValue

# A miss may be a DOI registered late or a registry's gap, so it is asked again after this long.
MISS_TTL = timedelta(days=30)


class RegistryResponse(BaseModel):
    """A cached answer: whether it was a hit, and the record or list the registry returned."""

    hit: bool
    response: JsonValue = None


class RegistryResponses:
    """Repository of cached registry answers; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def hits(self, source: str, keys: Sequence[str]) -> dict[str, bool]:
        """Whether each cached key was a hit, stale misses included; keys never asked are absent."""
        rows = self._conn.execute(
            "SELECT key, hit FROM registry_responses WHERE source = %s AND key = ANY(%s)",
            (source, list(keys)),
        ).fetchall()
        return {key: hit for key, hit in rows}

    def get(self, source: str, keys: Sequence[str]) -> dict[str, RegistryResponse]:
        """The answers that need not be asked again (as `fresh`), with what each returned."""
        rows = self._conn.execute(
            "SELECT key, hit, response FROM registry_responses WHERE source = %s"
            " AND key = ANY(%s) AND (hit OR fetched_at > now() - %s)",
            (source, list(keys), MISS_TTL),
        ).fetchall()
        return {key: RegistryResponse(hit=hit, response=response) for key, hit, response in rows}

    def fresh(self, source: str, keys: Sequence[str]) -> set[str]:
        """The keys that need not be asked again: hits, and misses younger than `MISS_TTL`."""
        rows = self._conn.execute(
            "SELECT key FROM registry_responses WHERE source = %s AND key = ANY(%s)"
            " AND (hit OR fetched_at > now() - %s)",
            (source, list(keys), MISS_TTL),
        ).fetchall()
        return {key for (key,) in rows}

    def record(self, source: str, key: str, response: JsonValue, *, hit: bool) -> None:
        """Keep a registry's answer for this key, replacing an earlier one."""
        self._conn.execute(
            "INSERT INTO registry_responses (source, key, response, hit) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (source, key) DO UPDATE SET response = EXCLUDED.response,"
            " hit = EXCLUDED.hit, fetched_at = now()",
            (source, key, Jsonb(response), hit),
        )
