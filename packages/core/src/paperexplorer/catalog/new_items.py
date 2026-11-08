"""A new Library Item's fields: the cached Crossref record, else OpenAlex's, else the row."""

import html
import re

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel, Field, JsonValue

from paperexplorer.domain import NewItem, RegistrySource, Work, normalize_doi
from paperexplorer.store import RegistryResponses

# Crossref's `type` and OpenAlex's as Zotero item types; any other is a `document`.
_CROSSREF_TYPES = {
    "journal-article": "journalArticle",
    "posted-content": "preprint",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "book-chapter": "bookSection",
    "proceedings-article": "conferencePaper",
}
_OPENALEX_TYPES = {
    "article": "journalArticle",
    "review": "journalArticle",
    "preprint": "preprint",
    "book": "book",
    "book-chapter": "bookSection",
}


class _CrossrefAuthor(BaseModel):
    family: str | None = None
    given: str | None = None
    name: str | None = None


class _CrossrefDate(BaseModel):
    date_parts: list[list[int | None]] = Field(default=[], alias="date-parts")


class _CrossrefItem(BaseModel):
    DOI: str | None = None
    type: str | None = None
    title: list[str] = []
    author: list[_CrossrefAuthor] = []
    issued: _CrossrefDate | None = None
    container_title: list[str] = Field(default=[], alias="container-title")
    institution: list[dict[str, JsonValue]] = []
    volume: str | None = None
    issue: str | None = None
    page: str | None = None


class _OpenAlexSource(BaseModel):
    display_name: str | None = None


class _OpenAlexLocation(BaseModel):
    source: _OpenAlexSource | None = None


class _OpenAlexBiblio(BaseModel):
    volume: str | None = None
    issue: str | None = None
    first_page: str | None = None
    last_page: str | None = None


class _OpenAlexAuthor(BaseModel):
    display_name: str | None = None


class _OpenAlexAuthorship(BaseModel):
    author: _OpenAlexAuthor | None = None


class _OpenAlexWork(BaseModel):
    doi: str | None = None
    type: str | None = None
    title: str | None = None
    display_name: str | None = None
    publication_date: str | None = None
    publication_year: int | None = None
    authorships: list[_OpenAlexAuthorship] = []
    primary_location: _OpenAlexLocation | None = None
    biblio: _OpenAlexBiblio | None = None


def new_item(conn: psycopg.Connection[TupleRow], work: Work) -> NewItem:
    """The new Library Item for a Work no Library Item holds, from what the registries said."""
    if work.doi:
        doi = normalize_doi(work.doi)
        responses = RegistryResponses(conn)
        crossref = responses.get(RegistrySource.CROSSREF_DOI, [doi]).get(doi)
        if crossref is not None and crossref.hit:
            return from_crossref(crossref.response)
        openalex = responses.get(RegistrySource.OPENALEX_DOI, [doi]).get(doi)
        if openalex is not None and openalex.hit:
            return from_openalex(openalex.response)
    return NewItem(
        title=work.title,
        creators=work.creators,
        date=str(work.year) if work.year else None,
        doi=work.doi,
        url=_doi_url(work.doi),
    )


def from_crossref(item: JsonValue) -> NewItem:
    """A Crossref `/works` item as a new Library Item."""
    it = _CrossrefItem.model_validate(item)
    parts = [p for p in (it.issued.date_parts[0] if it.issued and it.issued.date_parts else [])]
    date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p is not None)
    creators = [
        ", ".join(p for p in (a.family, a.given) if p) if a.family else a.name
        for a in it.author
        if a.family or a.name
    ]
    publication = it.container_title[0] if it.container_title else None
    if publication is None and it.institution:
        name = it.institution[0].get("name")
        publication = name if isinstance(name, str) else None  # a preprint's server
    return NewItem(
        item_type=_CROSSREF_TYPES.get(it.type or "", "document"),
        title=_text(it.title[0]) if it.title else None,
        creators=[t for c in creators if (t := _text(c))],
        date=date or None,
        doi=it.DOI,
        publication=_text(publication),
        volume=it.volume,
        issue=it.issue,
        pages=it.page,
        url=_doi_url(it.DOI),
    )


def from_openalex(work: JsonValue) -> NewItem:
    """An OpenAlex Work as a new Library Item."""
    w = _OpenAlexWork.model_validate(work)
    names = [a.author.display_name for a in w.authorships if a.author and a.author.display_name]
    source = w.primary_location.source if w.primary_location else None
    biblio = w.biblio or _OpenAlexBiblio()
    pages = "-".join(p for p in (biblio.first_page, biblio.last_page) if p) or None
    doi = normalize_doi(w.doi) if w.doi else None
    return NewItem(
        item_type=_OPENALEX_TYPES.get(w.type or "", "document"),
        title=_text(w.title or w.display_name),
        creators=[_last_first(t) for n in names if (t := _text(n))],
        date=w.publication_date or (str(w.publication_year) if w.publication_year else None),
        doi=doi,
        publication=_text(source.display_name) if source else None,
        volume=biblio.volume,
        issue=biblio.issue,
        pages=pages,
        url=_doi_url(doi),
    )


# Crossref's titles may hold JATS markup (`<i>in vivo</i>`) and HTML entities (`&amp;`).
_MARKUP = re.compile(r"</?[a-zA-Z][^<>]*>")


def _text(value: str | None) -> str | None:
    """A registry's string as plain text: markup dropped, entities read, spaces collapsed."""
    if value is None:
        return None
    plain = " ".join(html.unescape(_MARKUP.sub("", value)).split())
    return plain or None


def _doi_url(doi: str | None) -> str | None:
    return f"https://doi.org/{doi}" if doi else None


def _last_first(name: str) -> str:
    """OpenAlex's `First Last` written `Last, First`; a single name stays as it is."""
    first, _, last = name.strip().rpartition(" ")
    return f"{last}, {first}" if first else last
