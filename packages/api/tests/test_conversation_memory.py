from datetime import timedelta

import pytest

from paperexplorer.asking import Conversation
from paperexplorer.config import Settings
from paperexplorer.domain import Work, WorkId
from paperexplorer_api.conversations import Conversations

WORK = Work(
    id=WorkId(1),
    zotero_key="SEED0001",
    title=None,
    creators=[],
    year=None,
    doi=None,
    pdf=None,
    synced_at=None,
    text_origin=None,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_conversation_is_found_by_its_id() -> None:
    held = Conversations(limit=10, idle=timedelta(minutes=5))
    conversation = Conversation(work=WORK)

    held.add(conversation)

    assert held.get(conversation.id) is conversation
    assert held.get("no-such-id") is None


def test_a_conversation_idle_too_long_is_evicted() -> None:
    clock = Clock()
    held = Conversations(limit=10, idle=timedelta(minutes=5), clock=clock)
    kept, idle = Conversation(work=WORK), Conversation(work=WORK)
    held.add(kept)
    held.add(idle)

    clock.now = 4 * 60
    assert held.get(kept.id) is kept  # using it keeps it
    clock.now = 6 * 60

    assert held.get(idle.id) is None
    assert held.get(kept.id) is kept


def test_over_the_limit_the_least_recently_used_is_evicted() -> None:
    clock = Clock()
    held = Conversations(limit=2, idle=timedelta(minutes=5), clock=clock)
    first, second, third = (Conversation(work=WORK) for _ in range(3))
    held.add(first)
    clock.now = 1
    held.add(second)
    clock.now = 2
    held.get(first.id)
    clock.now = 3

    held.add(third)

    assert held.get(second.id) is None
    assert held.get(first.id) is first
    assert held.get(third.id) is third


def test_the_limits_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_CONVERSATIONS_MAX", "1")
    monkeypatch.setenv("PE_CONVERSATION_IDLE_MINUTES", "0.5")
    clock = Clock()
    held = Conversations.from_settings(Settings(_env_file=None), clock=clock)  # pyright: ignore[reportCallIssue]
    first, second = Conversation(work=WORK), Conversation(work=WORK)

    held.add(first)
    held.add(second)
    assert held.get(first.id) is None
    clock.now = 31

    assert held.get(second.id) is None
