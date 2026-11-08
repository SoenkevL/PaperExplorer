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

# Why no API client gets each field; the CLI and the eval read core's events directly.
_TURN_FIELDS_LEFT_OUT = {
    "trace_id": "ties a turn to its trace for the judges' scores; no API client scores a turn",
    "reading": "no API client tells the reader how the Seed was read",
    "model_requests": "a measure of the turn, for the CLI, the eval and the trace",
    "tool_calls": "a measure of the turn, for the CLI, the eval and the trace",
    "forced_search": "the eval's hit@8; an API client sees the search as a step",
}

LEFT_OUT: dict[type[BaseModel], dict[str, str]] = {
    asking.AnsweredEvent: {
        **_TURN_FIELDS_LEFT_OUT,
        "redrafted": "no API client tells a redrafted Answer from a first one",
    },
    asking.ErrorEvent: _TURN_FIELDS_LEFT_OUT,
    domain.Answer: {
        "unknown_handles": "already dropped from the Answer's text; no client shows them"
    },
    domain.AnswerSentence: {
        "substantive": "decides coverage and a redraft in core; no client uses it"
    },
    domain.Pointer: {
        "chunk_id": "the reader needs the Chunk's place, not its id",
        "similarity": "Support is what the reader is told of the cosine",
    },
}


def _fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields) | set(model.model_computed_fields)


def _union_members(union: object) -> set[object]:
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
    streamed = _union_members(events) | _union_members(asking.DraftPiece.__value__)

    assert streamed <= {core for core, _ in PAIRS}


def test_every_paired_event_and_draft_piece_is_in_the_contract_union() -> None:
    events, _ = get_args(asking.AnswerEvent.__value__)
    contract_events, _ = get_args(schemas.AnswerEvent.__value__)
    paired: dict[object, type[BaseModel]] = {core: contract for core, contract in PAIRS}

    assert {paired[core] for core in _union_members(events)} == _union_members(contract_events)
    assert {paired[core] for core in _union_members(asking.DraftPiece.__value__)} == _union_members(
        schemas.DraftPiece.__value__
    )


def test_a_draft_piece_cannot_validate_as_the_other_kind() -> None:
    # The contract's pieces have no discriminator, so their field names alone tell them apart.
    assert _fields(asking.TextPiece).isdisjoint(_fields(asking.HandlePiece))


def test_every_left_out_field_belongs_to_a_paired_model() -> None:
    assert set(LEFT_OUT) <= {core for core, _ in PAIRS}
