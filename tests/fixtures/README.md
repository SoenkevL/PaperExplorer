# Test fixtures

Open-access papers under Creative Commons Attribution licences, redistributed unchanged for testing. The Seed's PDF is not here: its licence is unknown, so it is used only by the `tower`-marked golden test, read from Zotero.

| File | Work | Licence | Why it is here |
|---|---|---|---|
| `pdfs/felippe2021-arxiv.pdf` | Felippe H., Viol A., de Araujo D. B., da Luz M. G. E., Palhano-Fontes F., Onias H., Raposo E. P., Viswanathan G. M. (2021). Threshold-free estimation of entropy from a Pearson matrix. arXiv:2106.05379 | CC BY 4.0 | Numbered `[n]` Citations with ranges (`[1–3]`), exact PDF link targets, TeX-split accents (`Barab´asi`), no References heading, a second bibliography restarting at [1] |
| `pdfs/villiger2024-tandf.pdf` | Villiger D. (2024). Personal psychedelic experience of psychedelic therapists during training: should it be required, optional, or prohibited? *International Review of Psychiatry* 36(8), 869–878. doi:10.1080/09540261.2024.2357669 | CC BY 4.0 | Author-year Citations, exact link targets on the years, link rectangles stored upside down, publisher cover page and masthead |
| `pdfs/wegner2023-rsquare.pdf` | von Wegner F., Wiemers M., Hermann G., Tödt I., Tagliazucchi E., Laufs H. (2023). Complexity measures for EEG microstate sequences: concepts and algorithms. Research Square preprint. doi:10.21203/rs.3.rs-2878411/v1 | CC BY 4.0 | LaTeX author-year Citations with exact links, line numbers in the margin, `2018a`/`2018b` year suffixes |
| `pdfs/albantakis2023-plos.pdf` | Albantakis L., Barbosa L., Findlay G., Grasso M., Haun A. M., Marshall W., et al. (2023). Integrated information theory (IIT) 4.0: Formulating the properties of phenomenal existence in physical terms. *PLoS Computational Biology* 19(10), e1011465. doi:10.1371/journal.pcbi.1011465 | CC BY 4.0 | Numbered Citations whose PDF links name only the target page |
| `pdfs/scan-villiger2024.pdf` | Pages 2, 7 and 8 of `villiger2024-tandf.pdf`, rendered to greyscale images at 150 dpi by `scripts/make_scan_fixture.py` | CC BY 4.0 (as the source) | A scan: no text layer, so parsing must fall back to OCR |

`grobid/<pdf stem>.tei.xml` holds GROBID 0.9.1 (CRF image) output for each PDF with a text layer, recorded by `scripts/record_grobid_fixtures.py`. Tests read these recordings instead of calling GROBID; one live test checks that a fresh GROBID still produces the same structure.

`tessdata/eng.traineddata` is Tesseract's fast English model (tesseract-ocr/tessdata_fast at commit 8741641, Apache-2.0), so OCR tests run offline. The pipeline image uses the more accurate tessdata_best model.

`handle-forms.json` is the table of handle forms that core's `asking` reads, in the resolved Answer and in the streamed draft (`docs/design-asking.md`, Answers): each draft written for the test around a form an eval run recorded, named by its trace, or a plain one. No words of the Seed or of an Answer are in it.
