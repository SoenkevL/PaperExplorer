"""Asking the Seed Questions: the answering agent, its Answers' Pointers and Quotes (ADR 0008)."""

from paperexplorer.asking.agent import REQUEST_LIMIT, AskingContext, ask
from paperexplorer.asking.answers import (
    has_words,
    resolve_answer,
    says_not_covered,
    wants_handles,
)
from paperexplorer.asking.conversation import Conversation
from paperexplorer.asking.draft import DraftPiece, HandlePiece, TextPiece
from paperexplorer.asking.events import (
    AnsweredEvent,
    AnswerEvent,
    DeltaEvent,
    ErrorEvent,
    RedraftEvent,
    StepEvent,
)
from paperexplorer.asking.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from paperexplorer.asking.reading import Reading
from paperexplorer.asking.tools import SeedTools

__all__ = [
    "PROMPT_VERSION",
    "REQUEST_LIMIT",
    "SYSTEM_PROMPT",
    "AnswerEvent",
    "AnsweredEvent",
    "AskingContext",
    "Conversation",
    "DeltaEvent",
    "DraftPiece",
    "ErrorEvent",
    "HandlePiece",
    "Reading",
    "RedraftEvent",
    "SeedTools",
    "StepEvent",
    "TextPiece",
    "ask",
    "has_words",
    "resolve_answer",
    "says_not_covered",
    "wants_handles",
]
