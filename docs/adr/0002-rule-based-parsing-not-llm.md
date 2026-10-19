---
status: tool choice superseded by ADR-0006; the rule that no generative model touches displayed text stands
---

# Rule-based parsing with LiteParse, not LLM parsing

PDFs are parsed with LiteParse (local, no LLM) and References and Citations are extracted with rules, resolved via Crossref and OpenAlex, with GROBID's citation parser only as a fallback for low-confidence Reference strings. The project's premise is to show what authors actually wrote, so no generative model may sit between the PDF and the text the reader sees. Generative models are limited to producing embeddings.

## Considered Options

- LLM/vision model via Ollama: rejected; can silently alter text.
- GROBID for everything: strong citation extraction, but chosen against in favour of LiteParse's layout/markdown output; kept as a narrow fallback.
