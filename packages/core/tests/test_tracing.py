import json
import logging
import uuid
from collections.abc import Callable

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from paperexplorer.config import Settings
from paperexplorer.tracing import Tracing, configure_tracing

TOWER = "http://pop-os.local:3000"
CLOUD = "https://cloud.langfuse.com"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        # A fresh key per test: the Langfuse SDK keeps one client per public key and process.
        "langfuse_public_key": f"pk-lf-test-{uuid.uuid4()}",
        "langfuse_secret_key": "sk-lf-test",
        "langfuse_base_url": TOWER,
        "langfuse_environment": "tower",
        "langfuse_release": "abc1234",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def tower(answer: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(answer))


def healthy(request: httpx.Request) -> httpx.Response:
    assert request.url == f"{TOWER}/api/public/health"
    return httpx.Response(200, json={"status": "OK", "version": "4.71.0"})


def down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("No route to host", request=request)


def test_tracing_is_off_without_keys() -> None:
    tracing = configure_tracing(
        settings(langfuse_public_key=None, langfuse_secret_key=None), http=tower(down)
    )

    assert tracing.base_url is None
    with tracing.trace("question", tags=["asking"], session_id="c1") as trace_id:
        assert trace_id is None


def test_traces_go_to_the_tower_when_it_answers() -> None:
    tracing = configure_tracing(settings(), http=tower(healthy))

    assert tracing.base_url == TOWER


@pytest.mark.parametrize(
    "answer",
    [down, lambda _: httpx.Response(503)],
    ids=["unreachable", "unhealthy"],
)
def test_traces_fall_back_to_cloud_when_the_tower_does_not_answer(
    answer: Callable[[httpx.Request], httpx.Response], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="paperexplorer.tracing"):
        tracing = configure_tracing(settings(), http=tower(answer))

    assert tracing.base_url == CLOUD
    assert f"{TOWER} does not answer" in caplog.text


def test_without_a_tower_traces_go_to_cloud() -> None:
    tracing = configure_tracing(settings(langfuse_base_url=None), http=tower(down))

    assert tracing.base_url == CLOUD


def recorded(tracing: Tracing, exporter: InMemorySpanExporter) -> tuple[ReadableSpan, ReadableSpan]:
    tracing.flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    return spans["question"], spans["chat paperexplorer-chat"]


def test_a_model_call_nests_inside_its_trace_and_carries_its_attributes() -> None:
    exporter = InMemorySpanExporter()
    tracing = configure_tracing(settings(), http=tower(healthy), span_exporter=exporter)

    with tracing.trace(
        "question",
        tags=["asking", "work:YAAC9XCX"],
        session_id="conversation-1",
        metadata={"position": "1"},
    ) as trace_id:
        # What pydantic-ai's instrumentation does with the provider it is handed.
        model = tracing.tracer_provider.get_tracer("pydantic-ai")
        with model.start_as_current_span(
            "chat paperexplorer-chat", attributes={"gen_ai.operation.name": "chat"}
        ):
            pass
    root, call = recorded(tracing, exporter)

    assert root.parent is None and root.context is not None
    assert trace_id == format(root.context.trace_id, "032x")
    assert call.parent is not None
    assert call.parent.span_id == root.context.span_id
    for span in (root, call):
        attributes = span.attributes or {}
        assert attributes["langfuse.trace.name"] == "question"
        assert attributes["session.id"] == "conversation-1"
        assert attributes["langfuse.trace.tags"] == ("asking", "work:YAAC9XCX")
        assert json.loads(str(attributes["langfuse.trace.metadata.position"])) == "1"
        assert span.resource.attributes["langfuse.environment"] == "tower"
        assert span.resource.attributes["langfuse.release"] == "abc1234"


def test_spans_and_scores_record_under_the_current_trace_and_nothing_outside_one() -> None:
    exporter = InMemorySpanExporter()
    ingested: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/scores":
            ingested.extend(json.loads(request.content))
            return httpx.Response(200, json={"id": "s1"})
        return healthy(request)

    tracing = configure_tracing(settings(), http=tower(answer), span_exporter=exporter)

    with tracing.span("outside", input="ignored") as outside:
        outside.update(output="ignored")
    tracing.score("outside", 1.0)
    with tracing.trace("question", tags=["asking"], session_id="conversation-1") as trace_id:
        with tracing.span("forced search", as_type="tool", input={"query": "entropy"}) as search:
            search.update(output="8 Chunks")
        with tracing.span("resolve answer"):
            pass
        tracing.score("pointer_coverage", 0.75)
    tracing.flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}

    assert "outside" not in spans
    root = spans["question"]
    assert root.context is not None
    for name, kind in [("forced search", "tool"), ("resolve answer", "span")]:
        span = spans[name]
        assert span.parent is not None and span.parent.span_id == root.context.span_id
        assert (span.attributes or {})["langfuse.observation.type"] == kind
    assert json.loads(str((spans["forced search"].attributes or {})["langfuse.observation.input"]))
    assert [(s["name"], s["value"], s["traceId"]) for s in ingested] == [
        ("pointer_coverage", 0.75, trace_id)
    ]
