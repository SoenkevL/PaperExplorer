"""Whether a printed DOI is registered: the `DoiRegistry` port and an in-memory adapter."""

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class Registration(StrEnum):
    """What doi.org says of a DOI; `unverified` when it could not be asked or did not answer."""

    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    UNVERIFIED = "unverified"


class DoiRecord(BaseModel):
    """One DOI's answer: its Registration and, when registered, the landing page it names."""

    registration: Registration
    url: str | None = None


class DoiRegistry(Protocol):
    """The record of every DOI (doi.org's Handle System), asked whether DOIs are registered."""

    def records(self, dois: Sequence[str]) -> dict[str, DoiRecord]:
        """One record per DOI asked, keyed as given; a DOI not answered is `unverified`."""
        ...


class InMemoryDoiRegistry:
    """DOIs given up front are registered, others not; `offline` leaves every DOI unverified.

    `asked` lists every DOI asked, in order, so tests can see what reached the registry.
    """

    def __init__(self, registered: Iterable[str] = (), *, offline: bool = False) -> None:
        self._registered = set(registered)
        self.offline = offline
        self.asked: list[str] = []

    def records(self, dois: Sequence[str]) -> dict[str, DoiRecord]:
        """Registered when given up front, unregistered otherwise, unverified when offline."""
        self.asked.extend(dois)
        if self.offline:
            return {d: DoiRecord(registration=Registration.UNVERIFIED) for d in dois}
        return {
            d: DoiRecord(registration=Registration.REGISTERED, url=f"https://example.org/{d}")
            if d in self._registered
            else DoiRecord(registration=Registration.UNREGISTERED)
            for d in dois
        }
