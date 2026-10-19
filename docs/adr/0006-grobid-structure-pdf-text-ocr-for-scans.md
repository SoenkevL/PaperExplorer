# GROBID finds the structure, the PDF's own text layer gives the words, OCR only for scans

PDFs are parsed in two passes. GROBID (CRF image, a compose service) decides where headings, paragraphs, sentences, References and Citation markers are. The characters under those positions are then read from the PDF's own text layer with pdfium, which also gives the PDF's internal link targets (Citation → Reference). Scans have no text layer and GROBID refuses them, so they are read by LiteParse's OCR and marked as OCR Text.

A spike on nine papers from five publishers ([research note](../research/2026-10-parsing-spike.md)) found:
- **LiteParse's layout is too fragile for structure:** paragraphs break at columns and pages, headings merge into body text, and bibliographies need per-publisher rules.
- **GROBID's structure is right on every layout,** but its text is rewritten (`[18-21]` → `[18][19][20][21]`, dropped spaces, split subscripts), which ADR 0002 forbids showing.
- **pdfium's characters under GROBID's boxes** matched GROBID's in 99% of paragraphs and kept the text as printed.

## Considered Options

- **LiteParse alone (ADR 0002):** rejected for structure; kept for OCR of scans.
- **GROBID alone:** rejected because it rewrites the printed text.
- **GROBID's deep-learning image:** linked fewer Citations correctly than the CRF image (588 vs 685 of 832) and needs 14.8 GB.

## Consequences

- **GROBID is a service the pipeline needs.** Tests read recorded GROBID output, and one live test checks that a fresh GROBID still gives the same structure.
- **Exactly two changes are made to the printed text:** whitespace becomes single spaces, and TeX's split accents are composed with their letter.
- **Every Citation Link records the evidence it rests on.** The PDF's own exact link target counts as certain.
