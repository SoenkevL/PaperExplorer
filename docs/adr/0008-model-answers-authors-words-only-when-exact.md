# A model may answer Questions about a Work; words count as the authors' only when they match exactly

ADR 0002 kept every generative model away from the text the reader sees. Asking the Seed questions changes that: a local model writes Answers in its own prose, so the reader can ask what the Seed measured, which methods it applied or what its shortcomings are, and land on the places that say so. The premise survives as a narrower rule: words in an Answer count as the authors' only when they match the stored text exactly, and everything else is visibly the model's.

- A sentence the model ties to a Chunk carries a Pointer to the sentence in the reader that best bears it out, marked by its Support: close or weak.
- Quotation marks are the model's prose. A Quote is shown as the authors' words only when it matches the text of a Chunk the model read in that turn, wherever the Pointer points; any other Quote is marked not exact.
- Answers draw on the Seed alone. A sentence the model ties to nothing stays unmarked.

## Considered Options

- **Extractive replies** (the model plans searches and picks Chunks; the reply is the Chunks): keeps ADR 0002 whole, but gives no natural answers.
- **A question box over retrieval, no chat model:** the same, without the agent.
- **Plain RAG chat that trusts the model's quotes:** what the project was started against.
- **One trust scale (verbatim > close > weak):** rejected; an exact Quote can sit in a sentence that misreads it, so the Quote's exactness and the sentence's Support are marked separately.

## Consequences

- ADR 0002's rule now reads: no generative model sits between the PDF and text shown as the authors'.
- A Pointer leads only to Chunks the model read in that turn, so it shows where the model found something, never where it could have.
- Quotes are matched with ligatures and line-break hyphens folded; the text shown stays as printed (ADR 0006).
