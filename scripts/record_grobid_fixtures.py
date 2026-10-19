"""Re-record `tests/fixtures/grobid/<pdf stem>.tei.xml` from a running GROBID.

Usage: uv run python scripts/record_grobid_fixtures.py [GROBID_URL]
Start GROBID first: docker run --rm -p 127.0.0.1:8070:8070 grobid/grobid:0.9.1-crf
Scans (`scan-*.pdf`) have no text layer and are skipped.
"""

import sys
import tempfile
from pathlib import Path

from paperexplorer.config import Settings
from paperexplorer.parsing.grobid import fetch_tei

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    """Send every fixture PDF through GROBID and store its TEI next to the other fixtures."""
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8070"
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(_env_file=None, data_dir=Path(tmp), grobid_url=url)  # pyright: ignore[reportCallIssue]
        for pdf in sorted((FIXTURES / "pdfs").glob("*.pdf")):
            if pdf.name.startswith("scan-"):
                continue
            tei = fetch_tei(pdf, settings)
            (FIXTURES / "grobid" / f"{pdf.stem}.tei.xml").write_bytes(tei)
            print(f"recorded {pdf.stem} ({len(tei) // 1024} KB)")


if __name__ == "__main__":
    main()
