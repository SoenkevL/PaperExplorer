"""Domain records shared across modules: Works, Library Items and the ids the store gives them."""

from datetime import datetime
from typing import NewType

from pydantic import BaseModel, ConfigDict

from paperexplorer.parsing.models import TextOrigin

WorkId = NewType("WorkId", int)
CitationId = NewType("CitationId", int)


class Attachment(BaseModel):
    """A PDF attached to a Library Item: its own Zotero key and file name."""

    model_config = ConfigDict(frozen=True)

    key: str
    filename: str


class LibraryItem(BaseModel):
    """The Zotero record of a Work: its metadata and, if any, its attached PDF."""

    key: str
    title: str | None = None
    creators: list[str] = []
    year: int | None = None
    doi: str | None = None
    pdf: Attachment | None = None


class Work(BaseModel):
    """A Work as PaperExplorer holds it: its Library Item's snapshot and how its text was read."""

    id: WorkId
    zotero_key: str | None
    title: str | None
    creators: list[str]
    year: int | None
    doi: str | None
    pdf: Attachment | None
    synced_at: datetime | None
    text_origin: TextOrigin | None
