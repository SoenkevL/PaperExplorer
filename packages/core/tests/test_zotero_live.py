import pytest

from paperexplorer.config import Settings
from paperexplorer.library import ZoteroLibrary

SEED = "YAAC9XCX"  # A critical review of brain entropy as a biomarker of the psychedelic state


@pytest.mark.tower
def test_the_real_library_has_the_seed_and_its_pdf() -> None:
    settings = Settings()
    if not settings.zotero_library_id or settings.zotero_api_key is None:
        pytest.skip("PE_ZOTERO_LIBRARY_ID / PE_ZOTERO_API_KEY are not set")
    library = ZoteroLibrary.from_settings(settings)

    item = library.item(SEED)

    assert item.title is not None
    assert "brain entropy" in item.title.lower()
    assert item.creators
    assert item.pdf is not None
    assert library.pdf(item.pdf).read_bytes().startswith(b"%PDF")
