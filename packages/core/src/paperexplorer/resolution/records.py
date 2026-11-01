"""Crossref and OpenAlex records read as `RegistryRecord`s and as matching `Candidate`s."""

from pydantic import BaseModel, Field, JsonValue

from paperexplorer.domain import RegistryRecord, normalize_doi
from paperexplorer.resolution.matching import Candidate


class _CrossrefAuthor(BaseModel):
    family: str | None = None
    given: str | None = None


class _CrossrefDate(BaseModel):
    date_parts: list[list[int | None]] = Field(default=[], alias="date-parts")


class _CrossrefItem(BaseModel):
    DOI: str | None = None
    type: str | None = None
    title: list[str] = []
    author: list[_CrossrefAuthor] = []
    issued: _CrossrefDate | None = None


class _OpenAlexAuthor(BaseModel):
    display_name: str | None = None


class _OpenAlexAuthorship(BaseModel):
    author: _OpenAlexAuthor | None = None


class _OpenAlexWork(BaseModel):
    id: str | None = None
    doi: str | None = None
    title: str | None = None
    display_name: str | None = None
    publication_year: int | None = None
    authorships: list[_OpenAlexAuthorship] = []


def from_crossref(item: JsonValue) -> RegistryRecord:
    """A Crossref `/works` item: its DOI, first title, authors and the year it was issued."""
    it = _CrossrefItem.model_validate(item)
    parts = it.issued.date_parts if it.issued else []
    return RegistryRecord(
        doi=normalize_doi(it.DOI) if it.DOI else None,
        title=it.title[0] if it.title else None,
        creators=[", ".join(p for p in (a.family, a.given) if p) for a in it.author if a.family],
        year=parts[0][0] if parts and parts[0] else None,
        kind=it.type,
    )


def from_openalex(work: JsonValue) -> RegistryRecord:
    """An OpenAlex Work: its DOI, short OpenAlex ID, title, authors and publication year."""
    w = _OpenAlexWork.model_validate(work)
    names = [a.author.display_name for a in w.authorships if a.author and a.author.display_name]
    return RegistryRecord(
        doi=normalize_doi(w.doi) if w.doi else None,
        openalex_id=w.id.rsplit("/", 1)[-1] if w.id else None,
        title=w.title or w.display_name,
        creators=[_last_first(n) for n in names],
        year=w.publication_year,
    )


def candidate(record: RegistryRecord) -> Candidate:
    """A registry record as the matching rules see it."""
    surnames = [c.split(",", 1)[0].strip() for c in record.creators]
    return Candidate(title=record.title, surnames=surnames, year=record.year, doi=record.doi)


def _last_first(name: str) -> str:
    """OpenAlex's `First Last` written `Last, First`; a single name stays as it is."""
    first, _, last = name.strip().rpartition(" ")
    return f"{last}, {first}" if first else last
