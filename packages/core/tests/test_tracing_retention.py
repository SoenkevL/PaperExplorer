import json
from datetime import UTC, datetime, timedelta

import httpx
from langfuse.api import LangfuseAPI

from paperexplorer.tracing.retention import prune

GB = 1_000_000_000
NOW = datetime(2026, 11, 20, tzinfo=UTC)


class FakeLangfuse:
    """The two Public API routes `prune` uses, with traces started one hour apart."""

    def __init__(self, traces: int, page_size: int = 7) -> None:
        self.roots = [
            {
                "id": f"root-{n}",
                "traceId": f"trace-{n:03}",
                "startTime": (NOW - timedelta(hours=traces - n)).isoformat(),
                "projectId": "paperexplorer",
                "type": "SPAN",
            }
            for n in range(traces)
        ]
        self.page_size = page_size
        self.deleted: list[list[str]] = []
        self.listings: list[httpx.QueryParams] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/public/v2/observations":
            self.listings.append(request.url.params)
            newest_first = sorted(self.roots, key=lambda r: r["startTime"], reverse=True)
            start = int(request.url.params.get("cursor", "0"))
            end = start + self.page_size
            cursor = str(end) if end < len(newest_first) else None
            return httpx.Response(
                200, json={"data": newest_first[start:end], "meta": {"cursor": cursor}}
            )
        if request.method == "DELETE" and request.url.path == "/api/public/traces":
            self.deleted.append(json.loads(request.content)["traceIds"])
            return httpx.Response(200, json={"message": "Traces deleted"})
        return httpx.Response(404)

    def api(self) -> LangfuseAPI:
        client = httpx.Client(transport=httpx.MockTransport(self))
        return LangfuseAPI(
            base_url="http://tower:3000", username="pk", password="sk", httpx_client=client
        )


def test_under_the_cap_nothing_is_deleted() -> None:
    langfuse = FakeLangfuse(traces=30)

    deleted = prune(langfuse.api(), size_bytes=5 * GB, cap_bytes=6 * GB, now=NOW)

    assert deleted == []
    assert langfuse.listings == []
    assert langfuse.deleted == []


def test_over_the_cap_the_oldest_tenth_is_deleted() -> None:
    langfuse = FakeLangfuse(traces=30)

    deleted = prune(langfuse.api(), size_bytes=7 * GB, cap_bytes=6 * GB, now=NOW)

    assert deleted == ["trace-000", "trace-001", "trace-002"]
    assert langfuse.deleted == [deleted]


def test_every_trace_is_listed_once_and_only_roots() -> None:
    langfuse = FakeLangfuse(traces=30, page_size=7)

    prune(langfuse.api(), size_bytes=7 * GB, cap_bytes=6 * GB, now=NOW)

    assert len(langfuse.listings) == 5
    assert all(params["isRootObservation"] == "true" for params in langfuse.listings)
    assert all(params["toStartTime"].startswith("2026-11-20") for params in langfuse.listings)


def test_one_trace_over_the_cap_is_still_deleted() -> None:
    langfuse = FakeLangfuse(traces=3)

    deleted = prune(langfuse.api(), size_bytes=7 * GB, cap_bytes=6 * GB, now=NOW)

    assert deleted == ["trace-000"]


def test_deletes_go_in_batches_the_api_accepts() -> None:
    langfuse = FakeLangfuse(traces=12_000, page_size=1000)

    deleted = prune(langfuse.api(), size_bytes=7 * GB, cap_bytes=6 * GB, now=NOW)

    assert len(deleted) == 1200
    assert [len(batch) for batch in langfuse.deleted] == [1000, 200]
