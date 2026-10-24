import pytest

from paperexplorer.citations import extract
from paperexplorer.config import Settings
from paperexplorer.parsing import BlockKind, TextOrigin, parse

SEED_ATTACHMENT = "YTS2XCEG"  # Zotero attachment of the Seed, item YAAC9XCX (licence unknown)


@pytest.mark.tower
def test_the_seed_parses_with_printed_text_and_linked_citations() -> None:
    settings = Settings()
    if settings.zotero_storage_dir is None:
        pytest.skip("PE_ZOTERO_STORAGE_DIR is not set")
    pdf = next((settings.zotero_storage_dir / SEED_ATTACHMENT).glob("*.pdf"))

    work = parse(pdf, settings)
    graph = extract(work)

    assert work.text_origin is TextOrigin.PRINTED
    assert sum(b.kind is BlockKind.HEADING for b in work.blocks) >= 15
    assert len(work.references) >= 182
    assert len(graph.citations) >= 280
    assert len(graph.unlinked()) <= 11  # 6 cite Carhart-Harris 2025, no dated entry in the list
    huxley = next(r.id for r in work.references if r.text.startswith("Huxley, A., 1959"))
    to_huxley = [c.text for c in graph.citations if c.link and c.link.reference_id == huxley]
    assert not [t for t in to_huxley if "Carhart-Harris" in t]
    printed = "(Erritzoe et al., 2024; McCulloch et al., 2022; Shinozuka et al., 2024)"
    assert any(printed in b.text for b in work.blocks)
