"""The eval's Question set: a pydantic-evals dataset of Questions with the places they expect."""

from collections.abc import Sequence
from pathlib import Path

from pydantic_evals import Dataset

from paperexplorer.evals.models import Place, QuestionMeta, Turn
from paperexplorer.store import StoredChunk

type Questions = Dataset[str, Turn, QuestionMeta]
"""Each case a Question (`inputs`) with its kind and expected places (`metadata`)."""


def load_questions(path: Path) -> Questions:
    """The Question set in `path`, a pydantic-evals dataset file (YAML or JSON)."""
    return Dataset[str, Turn, QuestionMeta].from_file(path)


def unknown_places(questions: Questions, chunks: Sequence[StoredChunk]) -> list[tuple[str, Place]]:
    """Each expected place, by Question, at which none of the Seed's Chunks sits."""
    known = {Place.of(c.chunk) for c in chunks}
    return [
        (case.name or "", place)
        for case in questions.cases
        if case.metadata is not None
        for place in case.metadata.places
        if place not in known
    ]
