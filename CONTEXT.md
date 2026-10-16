# PaperExplorer

A reading tool for following a paper's citations into the actual words of the works it cites. The reader explores; the tool retrieves, it does not summarise.

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

**Unresolved Reference**:
A Reference that could not be matched to any Work. It has no Library Item. Distinct from a resolved Work whose Text Availability is none.
_Avoid_: Missing source, noSource

**Citation**:
One place in a Work's body text where the authors point at exactly one Reference. A grouped marker such as "[3–5, 9]" is four Citations sharing one Citing Sentence.
_Avoid_: Reference, in-text reference, marker

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
One recorded search for Evidence: the Citing Sentence, the Scope, the parameters and the ranked Evidence returned.
_Avoid_: Query, search
