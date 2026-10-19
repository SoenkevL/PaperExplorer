from collections.abc import Callable
from pathlib import Path

import pytest

from paperexplorer.config import Settings
from paperexplorer.parsing import ParsedWork, parse

RECORDED = ["felippe2021-arxiv", "villiger2024-tandf", "wegner2023-rsquare", "albantakis2023-plos"]


def _structure(work: ParsedWork) -> tuple[object, ...]:
    markers = [
        (m.span, m.cue, m.grobid_reference_id, m.link_target)
        for b in work.blocks
        for m in b.citation_markers
    ]
    return (
        [(b.kind, b.heading_path, b.text) for b in work.blocks],
        [(r.id, r.label, r.text) for r in work.references],
        markers,
    )


@pytest.mark.parametrize("name", RECORDED)
def test_fresh_grobid_output_has_the_recorded_structure(
    name: str,
    grobid_url: str,
    recorded_settings: Settings,
    fixture_pdf: Callable[[str], Path],
    tmp_path: Path,
) -> None:
    live = Settings(_env_file=None, data_dir=tmp_path / "live", grobid_url=grobid_url)  # pyright: ignore[reportCallIssue]

    assert _structure(parse(fixture_pdf(name), live)) == _structure(
        parse(fixture_pdf(name), recorded_settings)
    )
