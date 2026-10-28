"""Keep the tower's trace store under its disk cap by deleting the oldest traces first.

Traces stay until the store reaches its cap or someone deletes them; there is no time-based
retention (`docs/design-asking.md`, Tracing).
"""

import logging
import math
from datetime import UTC, datetime
from itertools import batched

from langfuse.api import LangfuseAPI

logger = logging.getLogger(__name__)

# The Public API deletes at most this many traces per request.
DELETE_BATCH = 1000
# Listings must be time-bounded; nothing was traced before this.
SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def prune(
    api: LangfuseAPI, *, size_bytes: int, cap_bytes: int, now: datetime, share: float = 0.1
) -> list[str]:
    """Over the cap, delete the oldest `share` of traces (at least one); return their IDs.

    ClickHouse frees the space only after its merges, so a caller deletes one step per run
    and measures again on the next run instead of looping until the store is small enough.
    """
    if size_bytes <= cap_bytes:
        return []
    roots = _roots(api, now)
    count = max(1, math.ceil(len(roots) * share)) if roots else 0
    oldest = [trace_id for _, trace_id in sorted(roots)[:count]]
    for batch in batched(oldest, DELETE_BATCH, strict=False):
        api.trace.delete_multiple(trace_ids=list(batch))
    logger.info(
        "Trace store at %.2f of %.2f GB: deleted the oldest %d of %d traces",
        size_bytes / 1e9,
        cap_bytes / 1e9,
        len(oldest),
        len(roots),
    )
    return oldest


def _roots(api: LangfuseAPI, now: datetime) -> list[tuple[datetime, str]]:
    """Every trace as (start, trace ID), read from its root observation."""
    roots: list[tuple[datetime, str]] = []
    cursor: str | None = None
    while True:
        page = api.observations.get_many(
            fields="core",
            is_root_observation=True,
            from_start_time=SINCE,
            to_start_time=now,
            limit=1000,
            cursor=cursor,
        )
        roots += [(o.start_time, o.trace_id) for o in page.data if o.trace_id]
        cursor = page.meta.cursor
        if not cursor:
            return roots
