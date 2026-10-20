"""scripts/check_reader.py, which tower-check runs against freshly started containers."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_reader.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_reader", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_waits_while_a_just_started_container_resets_connections() -> None:
    # Regression, tower-check 2026-10-20: the first GET /works came within a second of
    # `docker compose up --wait` and got "Connection reset by peer" before uvicorn listened.
    calls: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) <= 2:
            raise httpx.ReadError("[Errno 104] Connection reset by peer", request=request)
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(api))

    _script().wait_until_up(http, "/health", timeout=5.0, pause=0.0)

    assert len(calls) == 3


def test_gives_up_when_the_service_never_answers() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(refuse))

    with pytest.raises(httpx.ConnectError):
        _script().wait_until_up(http, "/health", timeout=0.0, pause=0.0)
