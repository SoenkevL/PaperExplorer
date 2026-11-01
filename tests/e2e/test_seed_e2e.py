"""The Seed from the real Zotero library through every Stage into the API (tower only)."""

from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.embedding import OpenAICompatibleEmbedder
from paperexplorer.library import ZoteroLibrary
from paperexplorer.pipeline import STAGES, StageContext, run_stage
from paperexplorer.resolution import DoiOrgRegistry
from paperexplorer.store import Works
from paperexplorer.tracing import Tracing
from paperexplorer_api.app import create_app
from paperexplorer_api.client import ApiClient

SEED = "YAAC9XCX"


@pytest.mark.tower
def test_the_seed_reaches_the_api_with_its_citations(
    store_db: psycopg.Connection[TupleRow],
) -> None:
    settings = Settings()
    if not settings.zotero_library_id or settings.zotero_api_key is None:
        pytest.skip("PE_ZOTERO_LIBRARY_ID / PE_ZOTERO_API_KEY are not set")
    ctx = StageContext(
        conn=store_db,
        settings=settings,
        library=ZoteroLibrary.from_settings(settings),
        embedder=OpenAICompatibleEmbedder.from_settings(settings, Tracing.off()),
        tracing=Tracing.off(),
        doi_registry=DoiOrgRegistry.from_settings(settings),
    )
    work = Works(store_db).ensure(SEED)

    for stage in STAGES:
        run_stage(ctx, stage, work)

    text = ApiClient(TestClient(create_app(lambda: nullcontext(store_db)))).work(work)
    spans = [c for b in text.blocks for c in b.citations]
    assert text.text_origin == "printed"
    assert len(text.blocks) >= 100
    assert len(spans) >= 280
    # 6 cite Carhart-Harris 2025, which has no dated entry in the list (as the golden test)
    assert sum(c.confidence_level == "unlinked" for c in spans) <= 11
    assert set(text.stages.values()) == {"done"}
