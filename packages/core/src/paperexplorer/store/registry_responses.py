"""Registry responses: every answer Crossref and OpenAlex gave, so none is asked twice."""

from collections.abc import Collection
from typing import Any

import psycopg
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from paperexplorer.domain import RegistrySource

# A miss is asked again after this long: a DOI may be registered late.
MISS_RETRY = "30 days"


class RegistryResponse(BaseModel):
    """What a registry answered for one key: the record or list it returned, or nothing."""

    hit: bool
    body: Any = None


class RegistryResponses:
    """Repository of cached registry responses; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def get(self, source: RegistrySource, keys: Collection[str]) -> dict[str, RegistryResponse]:
        """The responses still good for these keys: every hit, and misses younger than 30 days."""
        rows = self._conn.execute(
            "SELECT key, hit, body FROM registry_responses"
            " WHERE source = %s AND key = ANY(%s)"
            " AND (hit OR fetched_at > now() - %s::interval)",
            (source.value, list(keys), MISS_RETRY),
        ).fetchall()
        return {key: RegistryResponse(hit=hit, body=body) for key, hit, body in rows}

    def put(self, source: RegistrySource, key: str, body: Any) -> None:
        """Keep what a registry answered for a key, `None` for a miss, over any earlier answer."""
        self._conn.execute(
            "INSERT INTO registry_responses (source, key, hit, body) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (source, key) DO UPDATE"
            " SET hit = EXCLUDED.hit, body = EXCLUDED.body, fetched_at = now()",
            (source.value, key, body is not None, None if body is None else Jsonb(body)),
        )
