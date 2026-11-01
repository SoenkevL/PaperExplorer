# PaperExplorer

A reading tool for following a paper's citations into the actual words of the works it cites. The reader explores; the tool retrieves. A model may answer the reader's Questions, but only words that match the authors' text exactly are shown as theirs.

## Works and the library

**Work**:
A single scholarly publication, identified by DOI, arXiv ID or OpenAlex ID.
_Avoid_: Paper, article, document, publication

**Version**:
A Work that is another edition of the same study as some other Work, e.g. a preprint and its published article. Versions are distinct Works; when a Reference could mean either, the published Version is preferred.
_Avoid_: Duplicate, copy, variant

**Library Item**:
The record of a Work in the Zotero library; the authoritative source of its metadata and attached PDF.
_Avoid_: Zotero entry, record

**Library**:
The reader's Zotero library: every Library Item in it, whether or not an Exploration uses it.
_Avoid_: Zotero, collection

**Exploration**:
The reading of one Seed together with the Works it cites, mirrored as one Zotero collection.
_Avoid_: Project, session, collection (when meaning the domain concept)

**Seed**:
The Work an Exploration starts from. A Seed always has full text.
_Avoid_: Root paper, main paper, original paper

**Source**:
A Work in the role of being cited by another Work. A role, not a kind of Work: a Source in one Exploration can be the Seed of another.
_Avoid_: Reference (see below), cited paper

**Text Availability**:
How much of a Work's own text PaperExplorer holds: full text, abstract only, or none.
_Avoid_: Access level, OA status

**OCR Text**:
A Work's text recovered by OCR from a scan. It may misread the authors' words, so it is never shown as their exact text.
_Avoid_: Scanned text

## Inside a Work

**Chunk**:
A contiguous span of a Work's text, at most one paragraph, that retrieval returns. Chunks of one paragraph may overlap by one sentence.
_Avoid_: Passage, snippet, segment

**Heading Path**:
The chain of section headings a Chunk sits under, e.g. "3 Methods > 3.2 Data".
_Avoid_: Section, breadcrumb

## Citing

**Reference**:
One entry in a Work's bibliography, as printed by its authors.
_Avoid_: Bibliography entry, source (see above), citation

**Resolution**:
The match of a Reference to a Work, with a confidence.
_Avoid_: Lookup, linking

**Registry Check**:
The reader's request to look one or more References up in Crossref and OpenAlex. It spends their rate limits, so it runs only when the reader asks; matching against the Library never needs one.
_Avoid_: Lookup, check (alone), query

**Unchecked Reference**:
A Reference that neither the Library nor an earlier Registry Check has matched to a Work, and that no Registry Check has looked up yet.
_Avoid_: Pending reference, unknown reference

**Unresolved Reference**:
A Reference that a Registry Check could not match to any Work. It has no Library Item. Distinct from an Unchecked Reference, which no Registry Check has looked up yet, and from a resolved Work whose Text Availability is none.
_Avoid_: Missing source, noSource

**DOI Conflict**:
A Reference whose printed DOI names a Work that its printed title, or without one its first surname and year, does not. The title then decides, at low confidence, and the DOI's Work stays the other option for the reader.
_Avoid_: Mismatch, bad DOI

**Reader's Choice**:
A Resolution the reader set: the Work they picked for a Reference, or none. Resolving and Registry Checks never overwrite it; only the reader clears it.
_Avoid_: Override, pin

**Citation**:
One place in a Work's body text where the authors point at exactly one Reference. A grouped marker such as "[3–5, 9]" is four Citations sharing one Citing Sentence.
_Avoid_: Reference, in-text reference, marker

**Citation Link**:
A Citation's tie to the Reference it points at, with a confidence and the evidence it rests on.
_Avoid_: Match, resolution (a Resolution ties a Reference to a Work)

**Unlinked Citation**:
A Citation that no evidence ties to a Reference.
_Avoid_: Unresolved citation, orphan citation

**Citing Sentence**:
The sentence a Citation occurs in, with the citation markers removed; the question asked of the cited Work.
_Avoid_: Context, query, citation context

## Retrieving

**Evidence**:
A Chunk returned as a candidate for what a Citing Sentence relies on, with its scores.
_Avoid_: Match, hit, result

**Scope**:
The set of Works searched for Evidence: the cited Work alone, or every Work in the Exploration.
_Avoid_: Filter, mode, whole collection

**Retrieval Run**:
One recorded search for Evidence: the Citing Sentence, the Scope, the parameters and the ranked Evidence returned. The searches a model runs to answer a Question are not Retrieval Runs.
_Avoid_: Query, search

## Asking

**Conversation**:
The reader's Questions and a model's Answers about one Work, in order.
_Avoid_: Chat, session, thread

**Question**:
What the reader asks in a Conversation.
_Avoid_: Query, prompt

**Answer**:
A model's reply to a Question; the only text written by a model that PaperExplorer shows.
_Avoid_: Response, summary

**Pointer**:
A mark that ties one sentence of an Answer to the place in a Chunk the model read that best bears it out, with its Support.
_Avoid_: Citation, reference, source, link

**Support**:
How well a Pointer's place bears out its sentence: close or weak.
_Avoid_: Confidence (a Citation Link has one), score

**Quote**:
Words an Answer puts in quotation marks. Exact when they match the authors' text in a Chunk the model read; only an exact Quote is shown as the authors' words.
_Avoid_: Citation, excerpt
