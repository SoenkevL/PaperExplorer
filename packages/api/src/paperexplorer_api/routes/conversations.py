"""Conversations: start one about a Work, then ask its Questions and stream the Answers."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from paperexplorer import asking
from paperexplorer.domain import Answer, WorkId
from paperexplorer.store import Chunks, Works
from paperexplorer_api import schemas
from paperexplorer_api.asking_thread import AskingContexts, AskingThread, still_answering
from paperexplorer_api.conversations import Conversations
from paperexplorer_api.routes import Connections

logger = logging.getLogger(__name__)


def router(
    connections: Connections,
    contexts: AskingContexts,
    conversations: Conversations,
    thread: AskingThread,
) -> APIRouter:
    """Routes under `/works/{id}/conversations` and `/conversations`."""
    routes = APIRouter(tags=["conversations"])

    @routes.post("/works/{work_id}/conversations", status_code=201)
    def start_conversation(work_id: int) -> schemas.Conversation:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            work = Works(conn).get(WorkId(work_id))
            if work is None:
                raise HTTPException(404, f"No Work {work_id}")
            chunks = Chunks(conn)
            if not chunks.for_work(work.id):
                raise HTTPException(409, f"Work {work_id} has no Chunks yet; run chunk and embed")
            model = contexts(conn).embedder.model
            if missing := chunks.without_embedding(work.id, model):
                raise HTTPException(
                    409,
                    f"{len(missing)} Chunks of Work {work_id} have no Vector from {model};"
                    " run embed",
                )
        conversation = asking.Conversation(work=work)
        conversations.add(conversation)
        logger.info("Started Conversation %s about Work %d", conversation.id, work.id)
        return schemas.Conversation(id=conversation.id)

    def answerable(conversation_id: str) -> asking.Conversation:
        """The Conversation, if it is held and not answering; else 404 or 409 before streaming."""
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(
                404, f"No Conversation {conversation_id}; it may have been evicted: start a new one"
            )
        if thread.answering(conversation_id):
            raise HTTPException(409, still_answering(conversation_id))
        return conversation

    @routes.post("/conversations/{conversation_id}/questions", response_class=EventSourceResponse)
    async def ask_question(  # pyright: ignore[reportUnusedFunction]
        question: schemas.Question,
        conversation: Annotated[asking.Conversation, Depends(answerable)],
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in thread.ask(conversation, question.text):
            sent = _event(event)
            yield ServerSentEvent(event=sent.type, raw_data=sent.model_dump_json())

    return routes


def _event(event: asking.AnswerEvent) -> schemas.AnswerEvent:
    match event:
        case asking.StepEvent(tool=tool, args=args):
            return schemas.StepEvent(tool=tool, args=args)
        case asking.DeltaEvent(text=text):
            return schemas.DeltaEvent(text=text)
        case asking.RedraftEvent():
            return schemas.RedraftEvent()
        case asking.AnsweredEvent(answer=answer):
            return schemas.AnsweredEvent(answer=_answer(answer))
        case asking.ErrorEvent(message=message):
            return schemas.ErrorEvent(message=message)


def _answer(answer: Answer) -> schemas.Answer:
    return schemas.Answer(
        text=answer.text,
        sentences=[
            schemas.AnswerSentence(
                start=s.start,
                end=s.end,
                pointers=[
                    schemas.Pointer(
                        block_id=p.block_id,
                        start=p.start,
                        end=p.end,
                        support=p.support.value,
                        chunk_start=p.chunk_start,
                        chunk_end=p.chunk_end,
                    )
                    for p in s.pointers
                ],
            )
            for s in answer.sentences
        ],
        quotes=[
            schemas.Quote(
                start=q.start,
                end=q.end,
                exact=q.exact,
                block_id=q.block_id,
                block_start=q.block_start,
                block_end=q.block_end,
            )
            for q in answer.quotes
        ],
    )
