"""The API contract of a Question's stream stays aligned with core's events, field by field.

The route validates the contract's events from core's by attribute name, so nothing else notices
a field added on one side or renamed on the other.
"""

from typing import get_args

import pytest
from pydantic import BaseModel

from paperexplorer import asking, domain
from paperexplorer_api import schemas

PAIRS: list[tuple[type[BaseModel], type[BaseModel]]] = [
    (asking.StepEvent, schemas.StepEvent),
    (asking.DeltaEvent, schemas.DeltaEvent),
    (asking.RedraftEvent, schemas.RedraftEvent),
    (asking.AnsweredEvent, schemas.AnsweredEvent),
    (asking.ErrorEvent, schemas.ErrorEvent),
    (asking.TextPiece, schemas.TextPiece),
    (asking.HandlePiece, schemas.HandlePiece),
    (domain.Answer, schemas.Answer),
    (domain.AnswerSentence, schemas.AnswerSentence),
    (domain.Pointer, schemas.Pointer),
    (domain.Quote, schemas.Quote),
]

_TURN_FIELDS_LEFT_OUT = {
    "trace_id": "the eval's and the judges' way to its trace; no client scores a turn",
    "reading": "the eval records it; the reader is not told how the Seed was read",
    "model_requests": "the eval's measure of the turn",
    "tool_calls": "the eval's measure of the turn",
    "forced_search": "the eval's hit@8; a client sees the search as a step",
}

LEFT_OUT: dict[type[BaseModel], dict[str, str]] = {
    asking.AnsweredEvent: {
        **_TURN_FIELDS_LEFT_OUT,
        "redrafted": "a client has already seen the redraft event",
    },
    asking.ErrorEvent: _TURN_FIELDS_LEFT_OUT,
    domain.Answer: {"unknown_handles": "dropped from the Answer; the eval counts them"},
    domain.AnswerSentence: {"substantive": "only Pointer coverage counts it"},
    domain.Pointer: {
        "chunk_id": "the reader needs the Chunk's place, not its id",
        "similarity": "Support is what the reader is told of the cosine",
    },
}


def _fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields) | set(model.model_computed_fields)


def _members(union: object) -> set[object]:
    return set(get_args(union))


@pytest.mark.parametrize(("core", "contract"), PAIRS, ids=lambda m: m.__qualname__)
def test_a_core_field_reaches_the_contract_or_is_left_out_on_purpose(
    core: type[BaseModel], contract: type[BaseModel]
) -> None:
    assert _fields(core) - _fields(contract) == set(LEFT_OUT.get(core, {}))


@pytest.mark.parametrize(("core", "contract"), PAIRS, ids=lambda m: m.__qualname__)
def test_a_contract_field_is_filled_from_the_core_field_of_its_name(
    core: type[BaseModel], contract: type[BaseModel]
) -> None:
    assert _fields(contract) <= _fields(core)


def test_every_event_and_draft_piece_of_core_has_its_pair() -> None:
    events, _ = get_args(asking.AnswerEvent.__value__)
    streamed = _members(events) | _members(asking.DraftPiece.__value__)

    assert streamed <= {core for core, _ in PAIRS}


def test_every_left_out_field_belongs_to_a_paired_model() -> None:
    assert set(LEFT_OUT) <= {core for core, _ in PAIRS}
