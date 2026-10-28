"""Langfuse tracing: where a process sends its traces, and the trace each model call runs inside.

`docs/architecture.md` (Tracing) gives the trace shape: one trace per Question, `stage.embed`
per embed run, every model call nested inside one of them.
"""

import logging
import subprocess
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager

import httpx
from langfuse import Langfuse, propagate_attributes
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import NoOpTracerProvider
from opentelemetry.trace import TracerProvider as AnyTracerProvider

from paperexplorer.config import Settings

logger = logging.getLogger(__name__)

# A process waits at most this long at start to learn whether the tower's Langfuse is up.
HEALTH_TIMEOUT_S = 2.0


class Tracing:
    """Where this process sends its traces; `configure_tracing` builds it once at start."""

    def __init__(
        self, langfuse: Langfuse | None, tracer_provider: AnyTracerProvider, base_url: str | None
    ) -> None:
        self._langfuse = langfuse
        self._tracer_provider = tracer_provider
        self._base_url = base_url

    @property
    def base_url(self) -> str | None:
        """The Langfuse host traces go to; None when tracing is off."""
        return self._base_url

    @property
    def tracer_provider(self) -> AnyTracerProvider:
        """Hand this to OpenTelemetry instrumentation (pydantic-ai) so its spans join the trace."""
        return self._tracer_provider

    @contextmanager
    def trace(
        self,
        name: str,
        *,
        tags: Sequence[str] = (),
        session_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Generator[str | None]:
        """Open a trace and yield its ID (None when off); observations inside nest under it."""
        if self._langfuse is None:
            yield None
            return
        with (
            self._langfuse.start_as_current_observation(name=name, as_type="span"),
            propagate_attributes(
                trace_name=name,
                tags=list(tags),
                session_id=session_id,
                metadata=dict(metadata or {}),
            ),
        ):
            yield self._langfuse.get_current_trace_id()

    def flush(self) -> None:
        """Send every finished observation now; short-lived processes call this before exit."""
        if self._langfuse is not None:
            self._langfuse.flush()


def configure_tracing(
    settings: Settings,
    *,
    http: httpx.Client | None = None,
    span_exporter: SpanExporter | None = None,
) -> Tracing:
    """Pick the tower's Langfuse, or the cloud when the tower does not answer; once per process."""
    public_key, secret_key = settings.langfuse_public_key, settings.langfuse_secret_key
    if public_key is None or secret_key is None:
        logger.info("Tracing is off: PE_LANGFUSE_PUBLIC_KEY and PE_LANGFUSE_SECRET_KEY are unset")
        return Tracing(None, NoOpTracerProvider(), None)
    if http is None:
        with httpx.Client() as client:
            base_url = _pick_base_url(settings, client)
    else:
        base_url = _pick_base_url(settings, http)
    environment, release = settings.langfuse_environment, settings.langfuse_release or _git_sha()
    # Our own provider, never the global one, so tests and other OpenTelemetry users stay apart.
    # Its resource carries environment and release to every span, as Langfuse's own provider does.
    resource = {"langfuse.environment": environment}
    if release:
        resource["langfuse.release"] = release
    tracer_provider = TracerProvider(resource=Resource.create(resource))
    langfuse = Langfuse(
        public_key=public_key,
        secret_key=secret_key.get_secret_value(),
        base_url=base_url,
        environment=environment,
        release=release,
        tracer_provider=tracer_provider,
        span_exporter=span_exporter,
    )
    logger.info("Tracing to %s as %s", base_url, environment)
    return Tracing(langfuse, tracer_provider, base_url)


def _pick_base_url(settings: Settings, http: httpx.Client) -> str:
    """The tower's Langfuse if its health endpoint answers, else the fallback."""
    tower, fallback = settings.langfuse_base_url, settings.langfuse_fallback_url
    if tower is None:
        return fallback
    tower = tower.rstrip("/")
    try:
        http.get(f"{tower}/api/public/health", timeout=HEALTH_TIMEOUT_S).raise_for_status()
    except httpx.HTTPError as error:
        logger.warning("Langfuse at %s does not answer (%s); tracing to %s", tower, error, fallback)
        return fallback
    return tower


def _git_sha() -> str | None:
    """The checked-out commit, or None outside a git checkout (containers)."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip() or None
