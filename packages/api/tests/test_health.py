from fastapi.testclient import TestClient

from paperexplorer import __version__
from paperexplorer_api.app import create_app
from paperexplorer_api.client import ApiClient


def test_health_reports_ok_and_version() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_client_parses_health_into_schema() -> None:
    client = ApiClient(TestClient(create_app()))

    assert client.health().version == __version__
