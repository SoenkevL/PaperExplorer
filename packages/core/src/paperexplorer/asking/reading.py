"""How a Question reads the Seed: whole, in context, when it fits; else by retrieval (ADR 0010)."""

from enum import StrEnum


class Reading(StrEnum):
    """How a Question reads the Seed: `auto` picks the whole Seed when it fits, else retrieval."""

    AUTO = "auto"
    WHOLE_SEED = "whole_seed"  # every Chunk in context under its handle; no tools, no search
    RETRIEVAL = "retrieval"  # the Seed's map, the forced search, then the tools

    def for_seed(self, tokens: int, budget: int) -> Reading:
        """The reading a Seed of `tokens` gets: `auto` reads it whole if `budget` holds them."""
        if self is not Reading.AUTO:
            return self
        return Reading.WHOLE_SEED if tokens <= budget else Reading.RETRIEVAL
