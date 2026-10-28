"""Copy a time window of traces and their scores from Langfuse Cloud to the tower's Langfuse.

There is no live sync: what a process traced to the cloud while the tower was down stays there
until this copies it over. Run it by hand from a machine whose .env has the PE_LANGFUSE_* settings:
    uv run python scripts/sync_langfuse.py --since 2026-11-02T08:00Z [--until 2026-11-02T18:00Z]
Adapted for SDK v5 from Langfuse's data-migration cookbook
(langfuse.com/guides/cookbook/example_data_migration). A copy keeps trace and observation IDs,
start and end times, nesting, names, types, input, output, metadata, model, usage and cost, and
each trace's name, session, user, tags, environment and release. Scores keep their IDs and
times. Copying a window twice writes the same IDs again, so repeating it is safe.
"""

import argparse
import base64
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Any

from langfuse.api import CreateScoreRequest, LangfuseAPI, ObservationV2, ScoreDataType
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from paperexplorer.config import Settings

FIELDS = "core,basic,time,io,metadata,model,usage,prompt,trace_context"
# Langfuse files the raw attributes of spans from other scopes under metadata; copies sent under
# the SDK's own scope keep the metadata the source observation had.
SCOPE = "langfuse-sdk"


class SourceIds(IdGenerator):
    """Hands the OpenTelemetry SDK the IDs each copied observation had at the source."""

    trace_id = 0
    span_id = 0

    def generate_trace_id(self) -> int:
        """The trace ID of the observation being copied."""
        return self.trace_id

    def generate_span_id(self) -> int:
        """The observation ID of the observation being copied."""
        return self.span_id


def pages(fetch: Any) -> Iterator[Any]:
    """Every row of a cursor-paginated listing; `fetch(cursor)` returns one page."""
    cursor: str | None = None
    while True:
        page = fetch(cursor)
        yield from page.data
        cursor = page.meta.cursor
        if not cursor:
            return


def text(value: Any) -> str:
    """An attribute value as Langfuse ingests it: strings as they are, the rest as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def nanoseconds(moment: datetime | None) -> int | None:
    """An OpenTelemetry timestamp, in integers so no float rounds it."""
    if moment is None:
        return None
    return (moment - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000


def attributes(o: ObservationV2) -> dict[str, Any]:
    """The span attributes Langfuse reads an observation and its trace's context from."""
    values: dict[str, Any] = {
        "langfuse.observation.type": o.type.lower(),
        "langfuse.trace.name": o.trace_name,
        "session.id": o.session_id,
        "user.id": o.user_id,
        "langfuse.environment": o.environment,
        "langfuse.release": o.release,
        "langfuse.version": o.version,
        "langfuse.observation.level": o.level,
        "langfuse.observation.status_message": o.status_message,
        "langfuse.observation.model.name": o.model,
        "langfuse.observation.prompt.name": o.prompt_name,
        "langfuse.observation.prompt.version": o.prompt_version,
    }
    if o.tags:
        values["langfuse.trace.tags"] = list(o.tags)
    for key, value in {
        "input": o.input,
        "output": o.output,
        "model.parameters": o.model_parameters,
        "usage_details": o.usage_details,
        "cost_details": o.cost_details,
    }.items():
        if value is not None:
            values[f"langfuse.observation.{key}"] = text(value)
    if o.completion_start_time is not None:
        values["langfuse.observation.completion_start_time"] = o.completion_start_time.isoformat()
    if isinstance(o.metadata, dict):
        for key, value in o.metadata.items():  # pyright: ignore[reportUnknownVariableType]
            values[f"langfuse.observation.metadata.{key}"] = text(value)
    return {k: v for k, v in values.items() if v not in (None, "", "{}", "[]")}


def otlp_provider(dest_url: str, public_key: str, secret: str) -> tuple[TracerProvider, SourceIds]:
    """A provider that sends spans to `dest_url`'s OTLP endpoint with the IDs set on SourceIds."""
    ids = SourceIds()
    provider = TracerProvider(id_generator=ids)
    token = base64.b64encode(f"{public_key}:{secret}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{dest_url.rstrip('/')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider, ids


def copy_trace(
    source: LangfuseAPI, tracer: otel_trace.Tracer, ids: SourceIds, trace_id: str
) -> int:
    """Re-send one trace's observations, parents first, with their own IDs; return how many."""
    observations = list(
        pages(
            lambda cursor: source.observations.get_many(
                trace_id=trace_id, fields=FIELDS, limit=1000, cursor=cursor
            )
        )
    )
    observations.sort(key=lambda o: (o.parent_observation_id is not None, o.start_time))
    ids.trace_id = int(trace_id, 16)
    for o in observations:
        ids.span_id = int(o.id, 16)
        parent = otel_context.Context()
        if o.parent_observation_id:
            parent = otel_trace.set_span_in_context(
                NonRecordingSpan(
                    SpanContext(
                        trace_id=ids.trace_id,
                        span_id=int(o.parent_observation_id, 16),
                        is_remote=True,
                        trace_flags=TraceFlags(TraceFlags.SAMPLED),
                    )
                )
            )
        span = tracer.start_span(
            o.name or o.type.lower(),
            context=parent,
            start_time=nanoseconds(o.start_time),
            attributes=attributes(o),
        )
        assert isinstance(span, Span)
        span.end(end_time=nanoseconds(o.end_time or o.start_time))
    return len(observations)


def copy_scores(source: LangfuseAPI, dest: LangfuseAPI, since: datetime, until: datetime) -> int:
    """Re-create the window's trace, observation and session scores; return how many."""
    requests: list[CreateScoreRequest] = []
    for s in pages(
        lambda cursor: source.scores.get_many(
            from_timestamp=since,
            to_timestamp=until,
            fields="details,subject",
            limit=100,
            cursor=cursor,
        )
    ):
        subject = s.subject
        if s.value is None or subject is None or subject.kind == "experiment":
            continue
        value = (1 if s.value else 0) if s.data_type == "BOOLEAN" else s.value
        trace_id = subject.id if subject.kind == "trace" else getattr(subject, "trace_id", None)
        requests.append(
            CreateScoreRequest(
                id=s.id,
                name=s.name,
                value=value,
                data_type=ScoreDataType(s.data_type),
                trace_id=trace_id,
                observation_id=subject.id if subject.kind == "observation" else None,
                session_id=subject.id if subject.kind == "session" else None,
                comment=s.comment,
                metadata=s.metadata,
                environment=s.environment,
                config_id=s.config_id,
                timestamp=s.timestamp,
            )
        )
    for batch in batched(requests, 100, strict=False):
        dest.scores.create(request=list(batch))
    return len(requests)


def main() -> int:
    """0 when every trace and score of the window was sent to the tower."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=datetime.fromisoformat)
    parser.add_argument("--until", type=datetime.fromisoformat, default=datetime.now(UTC))
    parser.add_argument("--source", help="default: PE_LANGFUSE_FALLBACK_URL (the cloud)")
    parser.add_argument("--dest", help="default: PE_LANGFUSE_BASE_URL (the tower)")
    args = parser.parse_args()
    settings = Settings()
    public_key, secret_key = settings.langfuse_public_key, settings.langfuse_secret_key
    source_url = args.source or settings.langfuse_fallback_url
    dest_url = args.dest or settings.langfuse_base_url
    if not (dest_url and public_key and secret_key):
        print("Set PE_LANGFUSE_BASE_URL and the PE_LANGFUSE_* keys in .env", file=sys.stderr)
        return 1
    secret = secret_key.get_secret_value()
    source = LangfuseAPI(base_url=source_url, username=public_key, password=secret)
    dest = LangfuseAPI(base_url=dest_url, username=public_key, password=secret)

    provider, ids = otlp_provider(dest_url, public_key, secret)
    tracer = provider.get_tracer(SCOPE)

    roots = pages(
        lambda cursor: source.observations.get_many(
            is_root_observation=True,
            from_start_time=args.since,
            to_start_time=args.until,
            fields="core",
            limit=1000,
            cursor=cursor,
        )
    )
    trace_ids = sorted({r.trace_id for r in roots if r.trace_id})
    observations = sum(copy_trace(source, tracer, ids, t) for t in trace_ids)
    provider.force_flush()
    provider.shutdown()
    scores = copy_scores(source, dest, args.since, args.until)
    print(
        f"Copied {len(trace_ids)} traces ({observations} observations) and {scores} scores "
        f"from {source_url} to {dest_url}, "
        f"{args.since:%Y-%m-%d %H:%M} to {args.until:%Y-%m-%d %H:%M}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
