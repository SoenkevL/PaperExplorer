"""Build `tests/fixtures/pdfs/scan-villiger2024.pdf`: two pages of a CC BY paper as images only.

Usage: uv run python scripts/make_scan_fixture.py
Pages 2 (abstract and introduction, with Citations) and 8 (References) of villiger2024-tandf.pdf
are rendered to greyscale JPEGs at 150 dpi, so the result has no text layer, like a scan.
"""

from pathlib import Path

import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pdfs"
PAGES = (2, 8)
DPI = 150


def main() -> None:
    """Render the pages and save them as an image-only PDF."""
    pdf = pdfium.PdfDocument(FIXTURES / "villiger2024-tandf.pdf")
    images = [pdf[n - 1].render(scale=DPI / 72, grayscale=True).to_pil() for n in PAGES]
    out = FIXTURES / "scan-villiger2024.pdf"
    images[0].save(out, "PDF", resolution=DPI, save_all=True, append_images=images[1:], quality=80)
    print(f"wrote {out.name} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
