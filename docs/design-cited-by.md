# Cited by: settled design (grilling outcome)

## Context
A Jump goes forward from a Citation, and the Trail goes back the way the reader came. A Source opened from the Library has no way back, as nothing shows who cites it. Grilling on 2026-11-08 settled "Cited by" and widened the Jump to go the other way.

## Scope and definition of done
- In scope: the list on a Work's page, a Jump to a Citing Work.
- Out of scope: spanning Versions (no Version relation exists), Works the reader does not hold, a Work citing itself.
- Done when:
  1. A Work's page lists the held Works whose firm Resolutions name it, each with its Citing Sentences.
  2. A Citing Sentence Jumps to its Citation, on the Trail, opening its Reference.
  3. A Citing Work with no linked Citation is listed, says so, and Jumps to its top.

## The list
- **Who:** a held Work (`zotero_key` set) with a firm Resolution (`Resolution.firm`, Readers' Choices included) naming exactly this Work.
- **Sentences:** every Citation linked to those References, in reading order, low-confidence links marked and OCR Text said to be. Two markers in one sentence (same Block, same words) are one row landing on the first, low if either link is, and both count.
- **Where:** a section below Library Matches in the Citation pane, newest year first (no year last, then title), each Citing Work's sentences folded. The sentence is the Jump.
- **When:** read as the page loads and each time its frame is shown (`trail:shown`), so a change in another frame appears on return; rows stay folded as the reader left them. Hidden when no Work cites this one; a failed read says so in place. On a narrow window a non-empty section brings up the sheet's button.

## The Jump to a Citing Work
- **Address:** `/works/{id}?at={citation}`. `?citation=` keeps its meaning: a Citation in another Work to search for here.
- **Landing:** scroll to the pill, mark it and open its Reference, as a click does, and clear any strip, cancelling a search still under way. There is no search or Embedder, so no strip of its own.
- **Nothing to land on:** with the pill gone (Citations extracted again, an old link) a strip says so, at the top.
- **No Citation to land on:** a click goes to the Work's top, even in a frame already open.
- **Trail:** a click lands even if the frame is already there; Back or Forward lands only if the entry's landing is not the frame's. A→B→A reuses A's frame, and going Back to the first A shows it as last left.

## The API
`GET /works/{id}/cited-by` answers `CitingWork[]`, each `{work, ocr, citations[] ({id, citing_sentence, confidence_level})}`; 404 for an unknown Work.

## Testing
- Store and API on real Postgres; the section in each state over a mocked API; one test through the real API and web app.
- In a browser, as no test runs JavaScript: the Jump, a stale link, Back, Forward, a repeated Jump after scrolling, a live frame, A→B→A, reload, a change arriving on return, a narrow window.
