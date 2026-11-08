# Jumping between a Work and its Sources: settled design (grilling outcome)

## Context
A reader on a Work can see which Sources they hold but cannot go to them from the page. A plain link would lose what they were doing: a Conversation's turns exist only in the page, and the sheet's scroll does not come back. Grilling on 2026-11-08 settled the Jump and the Trail, the first of the features making Explorations more interactive. ADR 0015 records the frames.

## Scope and definition of done
- In scope: *Jump to Source* in the Citation pane and the Library Matches list, recorded Retrieval Runs, the arrival strip, the Trail, Ingesting a Source from the pane.
- Out of scope: "Cited by" (the next feature), a words-only search, a Scope wider than one Work, fetching PDFs.
- Done when:
  1. A firm Source with text can be jumped to from a Citation, landing on its best Evidence, highlighted, with the top 5 to step through.
  2. Back, or a Work on the Trail, returns to the Work with its scroll, Citation and Conversation, and an Answer under way has gone on.
  3. A Source with no Evidence opens at its top and says why; one with no text says why in the pane, and offers Ingest when it has a PDF.

## The Jump
- **Who can be jumped to:** a Reference whose Resolution is firm (`Resolution.firm`), Readers' Choices included, naming a Work with text (parsed, `text_origin` set). `embed` is not required to open it.
- **From where:** the Resolution in the Citation pane, and each Library Matches entry. The entry uses the Citation stepped to in it, else its first. Clicking a Citation still only opens its Reference.
- **Landing:** the Citing Sentence is searched in the Source alone (`Scope.of`), and the sheet scrolls to the top Chunk. The strip shows the Citing Sentence, says this is a search's candidate and not what the authors relied on, steps through the top 5 and offers *Top of Source*.
- **No Evidence:** no Chunks, a Chunk without a Vector from the model, an unreachable Embedder, or no match lands at the top and says why. A search never runs on words alone, so a landing always means the same thing.

## Retrieval Runs
- `retrieval.find_evidence(conn, embedder, citation, scope, k=5)` reuses a recorded Run or searches and records one. A recorded Run is read before the Embedder is called, so it works with Ollama down.
- A Run is reused for the same Citation, Scope, model, `k` and `depth`. Replacing a Work's Chunks deletes the Runs whose Scope holds it, and a Run whose Evidence went with a new parse is not reused; changing a Citation's Resolution changes the Scope, so it searches again. Failed searches are not recorded. The Embedder may be slow, so the Chunks are checked again after it answers and held until the Run is recorded, and no Chunk replaced meanwhile ranks on words alone.
- Migration `0011_retrieval_runs.sql`: `retrieval_runs`, its Scope as rows in `retrieval_run_works`, and `evidence`, one row per Chunk with its ranks and scores.

## The API
`POST /citations/{id}/retrieval-runs` answers `{citation, citing_sentence, work, evidence[], reason}`, each Evidence `{rank, block, start, end, page, heading_path}`, and `reason` (`no_chunks`, `no_vectors`, `embedder_unreachable`, `no_match`) when there is none. 404 for an unknown Citation; 409 when it has no firm Resolution naming a Work with text. `WorkBrief` gains `has_text` and `has_pdf`; `Resolution` gains `firm`.

## The web app
- **Frames:** `/works/{id}` is the shell, one iframe per Work on the Trail, and `/works/{id}/reader` today's page (ADR 0015). The address is `/works/{id}?citation={c}`, so a reload or shared link lands the same way through the recorded Run.
- **Trail:** in each frame's top bar. Each entry returns with `history.go(-n)`, so Back and Forward stay coherent, and the middle collapses when long. A Jump from an earlier point cuts what followed, as browser history does. A→B→A shows all three and reuses A's frame.
- **Arrival:** the frame shows at once and says it is searching, then scrolls unless the reader has already scrolled. The strip's links are `a[data-block]`, painted as a Pointer is. With no Evidence it goes to the top. A frame already open says it is searching and asks again; going Back to an entry whose Citation is not the frame's asks too, so the address and the strip agree. The reader can close the strip.
- **No text yet:** with a PDF, the pane says so and offers Ingest, and shows *Jump to Source* once parsed, without leaving the page. With no PDF, it says so and links the DOI.

## Testing
- Core and API on real Postgres with `FakeEmbedder`: a Run recorded then reused with no embed call, deleted when Chunks are replaced, another for another Source; each `reason`; the 409s; the view fields.
- Web: the pane in each state, the strip with Evidence and with each reason, the shell and reader routes.
- In a browser, as no test covers frames: the Jump, the return, an Answer finishing while away, Forward from the Trail, reload, theme across frames, a narrow window, eviction.

## Delivery
Two PRs into `dev`: `feat/jump-to-source-api` (this design, ADR 0015, the terms, Retrieval Runs, the route) and `feat/jump-to-source-web` (the pane, the frames, the Trail, the strip).
