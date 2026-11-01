"""The doi.org adapter of `DoiRegistry`, over hand-written Handle API answers."""

import asyncio

import httpx
import pytest

from paperexplorer.resolution import DoiOrgRegistry, DoiRecord, Registration

FOUND = {
    "responseCode": 1,
    "handle": "10.5555/attention",
    "values": [
        {
            "index": 1,
            "type": "URL",
            "data": {"format": "string", "value": "https://example.org/attention"},
            "ttl": 86400,
            "timestamp": "2017-06-12T00:00:00Z",
        }
    ],
}


def _registry(handler: httpx.MockTransport, concurrency: int = 10) -> DoiOrgRegistry:
    return DoiOrgRegistry(transport=handler, concurrency=concurrency, backoff=0.0)


def test_handle_api_answers_become_registrations() -> None:
    answers = {
        "10.5555/attention": httpx.Response(200, json=FOUND),
        "10.5555/missing": httpx.Response(
            404, json={"responseCode": 100, "handle": "10.5555/missing"}
        ),
        "10.5555/no-url": httpx.Response(
            200, json={"responseCode": 200, "handle": "10.5555/no-url", "values": []}
        ),
        "10.5555/error": httpx.Response(500, json={"responseCode": 2}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "URL"
        return answers[request.url.path.removeprefix("/api/handles/")]

    records = _registry(httpx.MockTransport(handler)).records(list(answers))

    assert records == {
        "10.5555/attention": DoiRecord(
            registration=Registration.REGISTERED, url="https://example.org/attention"
        ),
        "10.5555/missing": DoiRecord(registration=Registration.UNREGISTERED),
        "10.5555/no-url": DoiRecord(registration=Registration.REGISTERED),
        "10.5555/error": DoiRecord(registration=Registration.UNVERIFIED),
    }


def test_a_5xx_is_tried_three_times_then_left_unverified() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=FOUND)

    registry = _registry(httpx.MockTransport(handler))
    assert registry.records(["10.5555/attention"])["10.5555/attention"].registration is (
        Registration.REGISTERED
    )

    calls.clear()
    always = _registry(httpx.MockTransport(lambda _: httpx.Response(503)))
    assert always.records(["10.5555/a"])["10.5555/a"].registration is Registration.UNVERIFIED


def test_no_connection_leaves_every_doi_unverified_and_stops_asking() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("No route to host", request=request)

    dois = [f"10.5555/{n}" for n in range(50)]
    records = _registry(httpx.MockTransport(handler), concurrency=1).records(dois)

    assert {r.registration for r in records.values()} == {Registration.UNVERIFIED}
    assert calls == 1


def test_a_doi_with_reserved_characters_is_sent_escaped() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json=FOUND)

    _registry(httpx.MockTransport(handler)).records(["10.1002/(sici)1097-4571#x;2-u?"])

    assert seen == ["/api/handles/10.1002/%28sici%291097-4571%23x%3B2-u%3F?type=URL"]


@pytest.mark.parametrize("concurrency", [1, 3])
def test_no_more_requests_are_in_flight_than_the_concurrency(concurrency: int) -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=FOUND)

    _registry(httpx.MockTransport(handler), concurrency).records([f"10.5555/{n}" for n in range(8)])

    assert peak == concurrency


@pytest.mark.tower
def test_doi_org_knows_real_dois_and_not_a_made_up_one() -> None:
    real = ["10.1038/s41593-025-02069-z", "10.48550/arXiv.1706.03762", "10.1093/nc/niac008"]

    records = DoiOrgRegistry().records([*real, "10.9999/paperexplorer.not.registered"])

    assert [records[d].registration for d in real] == [Registration.REGISTERED] * 3
    assert records["10.9999/paperexplorer.not.registered"].registration is (
        Registration.UNREGISTERED
    )
