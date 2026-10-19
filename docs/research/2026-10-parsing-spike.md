# Parsing spike: LiteParse, GROBID, pdfium link targets, OCR (October 2026)

The evidence behind [ADR 0006](../adr/0006-grobid-structure-pdf-text-ocr-for-scans.md). The throwaway scripts and the full findings are on the local branch `spike/parsing`.

## Corpus

Nine PDFs with text layers, plus one scan (Biagas 1999, six image-only pages, from the Zotero library):
- **The Seed:** Elsevier, author-year.
- **Four from the scico collection:**
  - Nature Reviews Neuroscience (superscript numbers);
  - PLOS Computational Biology (numbered);
  - Taylor & Francis (author-year);
  - a Research Square LaTeX preprint (author-year, line numbers).
- **Four arXiv papers:** two LaTeX numbered, two Word author-year.

## What each tool does

| | LiteParse 2.16 (OCR off) | GROBID 0.9.1 CRF | pdfium text under GROBID's boxes |
|---|---|---|---|
| Paragraphs | Break at columns and pages | Joined correctly | GROBID's |
| Headings | Merge with body text; levels unreliable | Correct on all nine, with section numbers | GROBID's |
| References | Need per-publisher rules (failed on Nature, PLOS, line-numbered preprint) | Correct on all nine, with fields | GROBID's boundaries, printed text |
| Text as printed | Close; TeX accents split, some scrambles | Rewritten: ranges expanded, spaces after `;` dropped, subscripts split | Close; TeX accents split (composed in code); 6 word-boundary errors in 95,299 words |
| Speed per paper | ~0.1 s | 2–4 s (CPU) | 1–2 s |

Reading pdfium's characters under GROBID's boxes reproduced the characters of 99.2% of GROBID's paragraphs and 99.8% of its References, while keeping `[18-21]`, `2024; McCulloch`, `5-HT2A` and typographic quotes as printed.

## Citation → Reference

- **Exact PDF link targets** (target page and position) appear in five of nine PDFs: LaTeX hyperref, Springer Nature, Taylor & Francis. They make 832 Citation→Reference pairs that serve as ground truth.
- **Page-only targets:** Elsevier and PLOS.
- **No links at all:** Word-made PDFs.
- **Against those 832 pairs, GROBID's own links scored:**
  - CRF image: 685 correct, 2 wrong;
  - deep-learning image: 588 correct, 2 wrong.

  The rest were gaps, not wrong links.
- **Merged entries:** GROBID merged two printed bibliography entries once in the fixtures. The hanging indent reveals it.

## OCR

- **GROBID refuses scans** (`NO_BLOCKS`).
- **LiteParse with Tesseract** reads a scan at about 1 s per page with a median word confidence of 0.96. Its errors hit names, Reference labels, dashes and page numbers (`Hurn` → `Hum`, `31` → `3`).
- **On PDFs with a text layer, OCR only adds noise:** logos, figure labels, garbled maths. Once it also turned an abstract into a table block.
- **So OCR runs only for scans.**
