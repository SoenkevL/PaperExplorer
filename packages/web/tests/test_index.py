import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_web.app import create_app


def _api(handler: httpx.MockTransport) -> ApiClient:
    return ApiClient(httpx.Client(base_url="http://api", transport=handler))


def test_index_shows_connected_api_version() -> None:
    api = _api(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok", "version": "9.9"}))
    )

    response = TestClient(create_app(api)).get("/")

    assert response.status_code == 200
    assert "API connected (v9.9)" in response.text


def test_index_reports_unreachable_api() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    response = TestClient(create_app(_api(httpx.MockTransport(refuse)))).get("/")

    assert response.status_code == 200
    assert "API unreachable" in response.text
