"""Relay a Registry Check's events from the API to the Work page, as server-sent events of HTML.

As with a Question (see `relay`), the page posts the Check to the web app, which holds it
(`relay.Pending`) until the page's EventSource GETs its stream; `relay_check` then starts the
Check at the API and re-sends each Reference's Resolution as it lands, then how the Check ended.
"""

import logging
from collections.abc import Generator, Mapping
from contextlib import closing

import jinja2
from pydantic import BaseModel

from paperexplorer_api.client import ApiClient, NotFoundError
from paperexplorer_api.schemas import (
    BudgetSpentEvent,
    CheckErrorEvent,
    CheckFinishedEvent,
    ReferenceCheckedEvent,
    Resolution,
)
from paperexplorer_web.relay import sse

logger = logging.getLogger(__name__)

LOST = (
    "This Check is no longer waiting to start (the page lost its stream, or the web app"
    " restarted); start it again. What it landed is kept."
)
ENDED = "The API's stream ended before the Check did; what it landed is kept. Start it again."
# How a finished Check's counts read, in the order the summary names them.
COUNTED = {
    "certain": "certain",
    "high": "high",
    "low": "low",
    "unchecked": "Unchecked",
    "unresolved": "Unresolved",
}


class PendingCheck(BaseModel):
    """A Check the Work page posted, waiting for its EventSource to start it."""

    work_id: int
    refresh: bool = False


def summary(counts: Mapping[str, int]) -> str:
    """A finished Check's counts in words: `2 References checked: 1 certain, 1 Unresolved`."""
    total = counts.get("references", 0)
    parts = [f"{counts[k]} {name}" for k, name in COUNTED.items() if counts.get(k)]
    if conflicts := counts.get("doi_conflicts", 0):
        parts.append(f"{conflicts} DOI Conflict{'' if conflicts == 1 else 's'}")
    checked = f"{total} Reference{'' if total == 1 else 's'} checked"
    return f"{checked}: {', '.join(parts)}" if parts else checked


def relay_check(
    api: ApiClient, check_id: str, check: PendingCheck | None, env: jinja2.Environment
) -> Generator[str]:
    """The Check's events as server-sent events of HTML: `reference`s, then one ending.

    Endings: `finished`, `budget_spent` or `error`; each replaces the running Check, which closes
    the page's EventSource, and lists again the References it landed. Closing this iterator
    closes the API's stream.
    """
    landed: list[Resolution] = []

    def ended(message: str, *, failed: bool, work_id: int | None) -> str:
        page = env.get_template("check_ended.html")
        return page.render(message=message, failed=failed, work_id=work_id, landed=landed)

    if check is None:
        yield sse("error", ended(LOST, failed=True, work_id=None))
        return
    work_id = check.work_id
    try:
        with closing(api.check(work_id, refresh=check.refresh)) as events:
            for event in events:
                match event:
                    case ReferenceCheckedEvent(resolution=resolution):
                        landed.append(resolution)
                        page = env.get_template("check_reference.html")
                        yield sse("reference", page.render(res=resolution))
                    case CheckFinishedEvent(counts=counts):
                        yield sse("finished", ended(summary(counts), failed=False, work_id=work_id))
                        return
                    case BudgetSpentEvent(message=message) | CheckErrorEvent(message=message):
                        yield sse(event.type, ended(message, failed=True, work_id=work_id))
                        return
    except NotFoundError as e:
        yield sse("error", ended(f"Not checked: {e}.", failed=True, work_id=work_id))
        return
    except Exception as e:
        # Whatever broke, the page must hear an end, or its EventSource asks again.
        logger.warning("The stream of Check %s broke off", check_id, exc_info=True)
        message = f"The Check's stream broke off ({e}); what it landed is kept."
        yield sse("error", ended(message, failed=True, work_id=work_id))
        return
    yield sse("error", ended(ENDED, failed=True, work_id=work_id))
