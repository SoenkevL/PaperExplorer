"""Typed HTTP client for the API; the web app's only way to reach data."""

from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import TypeAdapter

from paperexplorer_api.schemas import (
    AnswerEvent,
    CheckCost,
    CheckedReference,
    CheckEvent,
    CitationDetail,
    CollectionTree,
    Conversation,
    DuplicateItems,
    Health,
    Ingest,
    LibraryPage,
    LibrarySynced,
    LibraryWork,
    Merge,
    Merged,
    ReadersChoice,
    Resolution,
    WorkBrief,
    WorkSummary,
    WorkText,
)

_WORK_SUMMARIES = TypeAdapter(list[WorkSummary])
_ANSWER_EVENT: TypeAdapter[AnswerEvent] = TypeAdapter(AnswerEvent)
_CHECK_EVENT: TypeAdapter[CheckEvent] = TypeAdapter(CheckEvent)
_WORK_BRIEFS = TypeAdapter(list[WorkBrief])
_DUPLICATES = TypeAdapter(list[DuplicateItems])
# A turn's stream may sit idle while the model thinks; the API sends a keep-alive comment every
# 15 s, so only a minute without a byte means it is gone.
STREAM_TIMEOUT_S = 60.0
# A first sync reads the whole Library in pages, and another sync may hold it up.
SYNC_TIMEOUT_S = 300.0


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...
    @property
    def headers(self) -> Mapping[str, str]: ...
    def json(self, **kwargs: Any) -> Any: ...
    def raise_for_status(self) -> object: ...


class _StreamedResponse(_Response, Protocol):
    def iter_lines(self) -> Iterator[str]: ...
    def read(self) -> bytes: ...


class HttpClient(Protocol):
    """What `ApiClient` needs of an HTTP client: an `httpx.Client`, or Starlette's `TestClient`.

    Starlette's `TestClient` is an `httpx2.Client` once httpx2 is installed (pydantic-ai brings
    it), so the client is typed by what it does rather than by its class.
    """

    def get(self, url: str) -> _Response:
        """GET `url`, relative to the client's base URL."""
        ...

    def post(self, url: str, *, json: Any = None) -> _Response:
        """POST `json` to `url`."""
        ...

    def request(self, method: str, url: str, *, json: Any = None) -> _Response:
        """Send a request with any method, PUT and DELETE among them."""
        ...

    def stream(
        self, method: str, url: str, *, json: Any, timeout: float
    ) -> AbstractContextManager[_StreamedResponse]:
        """Send a request and read its response as it arrives, within the context."""
        ...


class NotFoundError(LookupError):
    """The API has no such Work, Citation or Conversation (HTTP 404)."""


class ConflictError(RuntimeError):
    """The API cannot do it yet (HTTP 409): a Work not embedded, a Conversation still answering."""


class ApiClient:
    """Thin typed wrapper over the API's JSON endpoints and its Questions' event streams."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @classmethod
    def from_url(cls, base_url: str, *, timeout: float = 10.0) -> ApiClient:
        """Client for an API at `base_url`."""
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    def health(self) -> Health:
        """Fetch the API's health."""
        return Health.model_validate(self._get("/health"))

    def works(self) -> list[WorkSummary]:
        """Every Work, with its Stage status."""
        return _WORK_SUMMARIES.validate_python(self._get("/works"))

    def work(self, work_id: int) -> WorkText:
        """One Work with its text and Citation spans; `NotFoundError` if it has no text."""
        return WorkText.model_validate(self._get(f"/works/{work_id}"))

    def citation(self, citation_id: int) -> CitationDetail:
        """One Citation with its Citation Link and Reference."""
        return CitationDetail.model_validate(self._get(f"/citations/{citation_id}"))

    def start_conversation(self, work_id: int) -> Conversation:
        """A new Conversation about a Work; `ConflictError` until its Chunks are embedded."""
        path = f"/works/{work_id}/conversations"
        response = self._http.post(path)
        _check(response, path)
        return Conversation.model_validate(response.json())

    def ask(self, conversation_id: str, text: str) -> Generator[AnswerEvent]:
        """Ask a Question: its steps and draft as they come, then the Answer or an error.

        The request goes out when iteration starts, and `NotFoundError` (no such Conversation,
        or evicted) or `ConflictError` (still answering) are raised then. Closing the iterator
        early closes the stream, which cancels the turn.
        """
        path = f"/conversations/{conversation_id}/questions"
        with self._http.stream(
            "POST", path, json={"text": text}, timeout=STREAM_TIMEOUT_S
        ) as response:
            if response.status_code >= 400:
                response.read()
            _check(response, path)
            yield from _events(response.iter_lines(), _ANSWER_EVENT)

    def check_cost(self, work_id: int) -> CheckCost:
        """What a Registry Check of the Work would ask, and what is left of OpenAlex's day."""
        return CheckCost.model_validate(self._get(f"/works/{work_id}/checks/cost"))

    def check(self, work_id: int, *, refresh: bool = False) -> Generator[CheckEvent]:
        """Check the Work's References: each one's Resolution as it lands, then how it ended.

        The request goes out when iteration starts; closing the iterator early closes the stream.
        """
        path = f"/works/{work_id}/checks"
        with self._http.stream(
            "POST", path, json={"refresh": refresh}, timeout=STREAM_TIMEOUT_S
        ) as response:
            if response.status_code >= 400:
                response.read()
            _check(response, path)
            yield from _events(response.iter_lines(), _CHECK_EVENT)

    def check_reference(self, work_id: int, key: str, *, refresh: bool = False) -> CheckedReference:
        """Check one Reference; `refresh` asks the registries again, past the cache."""
        path = f"/works/{work_id}/references/{key}/checks"
        # Not a stream, but it can wait as long as one: Checks queue for the registries' limits.
        with self._http.stream(
            "POST", path, json={"refresh": refresh}, timeout=STREAM_TIMEOUT_S
        ) as response:
            response.read()
            _check(response, path)
            return CheckedReference.model_validate(response.json())

    def candidates(self, work_id: int, key: str) -> list[WorkBrief]:
        """The Works a reader might choose for a Reference, likeliest first."""
        return _WORK_BRIEFS.validate_python(
            self._get(f"/works/{work_id}/references/{key}/candidates")
        )

    def choose(self, work_id: int, key: str, choice: ReadersChoice) -> Resolution:
        """Set the Reader's Choice for a Reference: its Resolution as it now stands."""
        path = f"/works/{work_id}/references/{key}/choice"
        return Resolution.model_validate(self._send("PUT", path, choice.model_dump()))

    def clear_choice(self, work_id: int, key: str) -> Resolution:
        """Clear the Reader's Choice for a Reference: its Resolution resolved again."""
        path = f"/works/{work_id}/references/{key}/choice"
        return Resolution.model_validate(self._send("DELETE", path))

    def duplicates(self) -> list[DuplicateItems]:
        """Each Work's Duplicate Items, its Standing Item first."""
        return _DUPLICATES.validate_python(self._get("/duplicates"))

    def let_stand(self, zotero_key: str) -> list[DuplicateItems]:
        """Let this Duplicate Item stand for its DOI; the Duplicate Items as they now stand."""
        return _DUPLICATES.validate_python(self._send("PUT", f"/duplicates/{zotero_key}/stand"))

    def merge(self, merge: Merge) -> Merged:
        """Merge one Duplicate Item into another in Zotero; the other goes to the trash."""
        return Merged.model_validate(self._send("POST", "/duplicates/merge", merge.model_dump()))

    def library(
        self,
        query: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
        collection: str | None = None,
        deep: bool = False,
        unfiled: bool = False,
    ) -> LibraryPage:
        """A page of the Library Items matching `query`, Ingested ones first.

        `collection` (its branch with `deep`) or `unfiled` picks a branch: with no query only its
        Items, with one the whole Library, the branch's first; `NotFoundError` for an unknown key.
        """
        params: dict[str, str | int] = {"q": query, "limit": limit, "offset": offset}
        if collection is not None:
            params["collection"] = collection
        if deep:
            params["deep"] = "true"
        if unfiled:
            params["unfiled"] = "true"
        return LibraryPage.model_validate(self._get(f"/library?{urlencode(params)}"))

    def collections(self) -> CollectionTree:
        """Every Collection with its branch's counts, the whole Library's, and the unfiled ones."""
        return CollectionTree.model_validate(self._get("/collections"))

    def sync_library(self) -> LibrarySynced:
        """Bring the Library up to date with Zotero."""
        path = "/library/sync"
        with self._http.stream("POST", path, json=None, timeout=SYNC_TIMEOUT_S) as response:
            response.read()
            _check(response, path)
            return LibrarySynced.model_validate(response.json())

    def work_stages(self, work_id: int) -> LibraryWork:
        """One Library Item with where each of its Stages stands."""
        return LibraryWork.model_validate(self._get(f"/works/{work_id}/stages"))

    def ingest(self, work_id: int, *, from_stage: str | None = None) -> LibraryWork:
        """Start Ingesting; `ConflictError` with no PDF, nothing left to run, or a run under way."""
        body = Ingest(from_stage=from_stage).model_dump()
        return LibraryWork.model_validate(self._send("POST", f"/works/{work_id}/ingesting", body))

    def cancel_stage_run(self, work_id: int) -> None:
        """Cancel a Work's waiting Stage Run; `ConflictError` once it is under way."""
        path = f"/works/{work_id}/stage-run"
        _check(self._http.request("DELETE", path), path)

    def resolve_references(self, work_id: int) -> LibraryWork:
        """Start the Stages that resolve an Ingested Work's References."""
        return LibraryWork.model_validate(self._send("POST", f"/works/{work_id}/resolving"))

    def _get(self, path: str) -> Any:
        response = self._http.get(path)
        _check(response, path)
        return response.json()

    def _send(self, method: str, path: str, json: Any = None) -> Any:
        response = self._http.request(method, path, json=json)
        _check(response, path)
        return response.json()


def _check(response: _Response, path: str) -> None:
    """Raise `NotFoundError` on 404, `ConflictError` on 409, the HTTP client's error otherwise."""
    if response.status_code in (404, 409):
        is_json = response.headers.get("content-type", "").startswith("application/json")
        detail = response.json().get("detail", path) if is_json else path
        raise (NotFoundError if response.status_code == 404 else ConflictError)(detail)
    response.raise_for_status()


def _events[E](lines: Iterable[str], adapter: TypeAdapter[E]) -> Iterator[E]:
    """The event in each server-sent event's data; comments (keep-alives) are skipped.

    The event's name repeats its data's `type`, so the data alone is read.
    """
    data: list[str] = []
    for line in lines:
        if not line:
            if data:
                yield adapter.validate_json("\n".join(data))
            data = []
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value.removeprefix(" "))
