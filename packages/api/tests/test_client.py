import pytest
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient, NotFoundError

from .conftest import Stored


def test_the_client_reads_works_and_citations_into_schemas(
    api: TestClient, villiger: Stored
) -> None:
    client = ApiClient(api)

    [summary] = client.works()
    work = client.work(summary.id)
    span = next(c for b in work.blocks for c in b.citations)
    citation = client.citation(span.id)

    assert summary.zotero_key == "VILL2024"
    assert len(work.blocks) == len(villiger.parsed.blocks)
    assert citation.id == span.id
    assert citation.work_id == work.id


def test_the_client_raises_not_found(api: TestClient) -> None:
    with pytest.raises(NotFoundError):
        ApiClient(api).work(999999)
    with pytest.raises(NotFoundError):
        ApiClient(api).citation(999999)
