# Quote-first Answers: settled design (grilling outcome)

## Context
A live Question on 2026-10-31 (trace `8997449f`, release `5d5b5fa`, before ADR 0010 reached the tower) streamed a good draft and then failed: retrieval spent its six requests on tool calls with wrong handles and References, the sixth wrote its Answer inside its thinking, and pydantic-ai's retry for a thinking-only response hit the request limit. Since then PR #21 turned thinking off on requests without tools and added a redraft that asks the model again for its handles. The `[#n]` contract keeps needing patches: a lenient handle grammar, bare `[n]`, the redraft. One grilling round with the user (2026-10-31) settled the design below. It replaces handles with Quotes as the Answer's tie to the Seed, and only after an eval win (Adoption).

## What the model writes
- One free-text Answer per Question: no tools, no thinking, no handles, on both readings (whole Seed and retrieval).
- It argues from the Seed: every claim rests on its words. It weighs the Seed's claims only when the reader asks for it.
- Each claim in the model's own words, then the Quote that bears it out, so the sentence reads whole when the Quote becomes a chip.
- **Retrieval without tools:** before the one request the server runs a hybrid search for the Question (k≈12) and widens each hit with the Chunk before and after it, in reading order, up to a token budget; the Seed's map and abstract stay in context. No model call comes before the Answer.

## What the server checks
- **Quotes.** Each double-quoted span is aligned word by word, folded as now (ligatures, line-break hyphens), against the Chunks in context. It may run on into the next Chunk in reading order; an ellipsis splits it into parts found in order. Found when about 90% of its words align, the threshold set from the eval. What the reader sees is the authors' printed span, from the first aligned word to the last, never the model's version, so shown words stay the authors' exactly (ADR 0008).
- **The fix.** If any Quote is not found: one request without tools or thinking, the Answer, then a note listing each such Quote; the model gives an exact replacement from the Seed or "none". Replacements are spliced in and checked again; the rest of the Answer stays as written. A Quote still not found is kept, marked not found in the Seed.
- **Pointers.** A sentence with a found Quote points at the Quote's place. Every other substantive sentence is matched by bge-m3 against the sentences of the Chunks in context and gets a Pointer only at close Support (≥ 0.7, `PE_SUPPORT_THRESHOLD`); below it stays unmarked. One mark per sentence.
- **The Conversation** keeps each Answer with every found Quote replaced by its printed text, so later Questions build on the authors' words.

## What the reader sees
- While the draft streams: its raw text, Quotes included.
- While the fix runs: the draft stays, with "Checking Quotes…" above it (a stream event, as the redraft's is now).
- The resolved Answer replaces the draft. Each Quote is a chip with the printed text in a hover or tap preview; a click scrolls to its place, the first part if it runs over two blocks, and highlights every part; the preview joins the parts with a paragraph break. A not-found Quote's chip says so and previews the model's words. Pointer chips stay as now, close only.

## What goes
The handle grammar and `tests/fixtures/handle-forms.json`, the bare-`[n]` rule, the redraft, retrieval's agent loop with its request limit and last-request note, and the five reading tools.

## Adoption
- Built on its own feature branch.
- The eval runs with repeats on `dev` (handles and redraft), then on the branch.
- **Bar:** judged grounded and correct ≥ 80%, pooled over repeats, is the gate. Beside it: Quotes found after the fix ≥ 90%, Pointer coverage ≥ 0.8 from the embedding Pointers, hit@8 on retrieval, and no shown Quote that differs from the printed text (checked).
- It replaces handles only if it matches or beats `dev` on the gate. Then ADR 0008 is amended (Quotes found closely, shown as printed), `CONTEXT.md`'s Quote and Pointer are redefined, and `design-asking.md` and ADR 0010's retrieval tools are updated.

## Before building
- Redeploy the tower: it served `5d5b5fa`, without ADR 0010 or PR #21.
- Run the `dev` eval with repeats for the comparison. Done: run `20261031T195055Z-f349d1e`, judged 59 of 66 live Answers grounded and correct (89%; 82, 100 and 86% by repeat) and 18 of 22 by retrieval, Pointer coverage 0.66 (0.53 to 0.74 by repeat), and not one Quote in 88 Answers.
- Prototype the prompt first. With quotation marks allowed today, the model wrote none, so quote-first rests on a prompt that makes a 12B model quote by default. Ask the eval's Questions with only the prompt changed and count Quotes and found Quotes before building the matching, the fix and the chips.
