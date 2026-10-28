"""FastAPI + Jinja application factory for the web app."""

import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from paperexplorer_api.client import ApiClient, ConflictError, NotFoundError
from paperexplorer_api.schemas import Block, CitationDetail, WorkSummary
from paperexplorer_web.relay import PendingQuestion, PendingQuestions, Views, not_embedded, relay
from paperexplorer_web.segments import Segment, segments

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
# How the panel names each kind of evidence a Citation Link rests on.
EVIDENCE = {
    "pdf_link": "the PDF's own link",
    "grobid": "GROBID",
    "label": "the printed label",
    "author_year": "author and year",
}


class BlockView(BaseModel):
    """A block ready to render: its HTML element and its text cut at Citation spans."""

    block: Block
    tag: str
    segments: list[Segment]


def _view(block: Block) -> BlockView:
    # h1 is the Work's title; a heading sits one level below the headings on its path
    tag = f"h{min(len(block.heading_path) + 2, 6)}" if block.kind == "heading" else "p"
    return BlockView(block=block, tag=tag, segments=segments(block.text, block.citations))


def _panel_url(work_id: int, citations: list[int]) -> str:
    return f"/works/{work_id}/panel?{urlencode([('c', c) for c in citations])}"


def create_app(api: ApiClient) -> FastAPI:
    """Build the web app on top of an API client."""
    app = FastAPI(title="PaperExplorer")
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.globals["panel_url"] = _panel_url  # pyright: ignore[reportUnknownMemberType]
    templates.env.globals["evidence"] = EVIDENCE  # pyright: ignore[reportUnknownMemberType]
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    def not_found(request: Request, message: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "not_found.html", {"message": message}, status_code=404
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        works: list[WorkSummary] = []
        try:
            api_version: str | None = api.health().version
            works = api.works()
        except httpx.HTTPError:
            logger.warning("API unreachable", exc_info=True)
            api_version = None
        return templates.TemplateResponse(
            request, "index.html", {"api_version": api_version, "works": works}
        )

    @app.get("/works/{work_id}", response_class=HTMLResponse)
    def work(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            text = api.work(work_id)
        except NotFoundError as e:
            return not_found(request, str(e))
        return templates.TemplateResponse(
            request, "work.html", {"work": text, "blocks": [_view(b) for b in text.blocks]}
        )

    @app.get("/works/{work_id}/panel", response_class=HTMLResponse)
    def panel(  # pyright: ignore[reportUnusedFunction]
        request: Request, work_id: int, c: Annotated[list[int], Query()]
    ) -> HTMLResponse:
        try:
            details = [api.citation(i) for i in c]
        except NotFoundError as e:
            return not_found(request, str(e))
        if any(d.work_id != work_id for d in details):
            return not_found(request, f"Not all of {c} are Citations of Work {work_id}")
        return templates.TemplateResponse(request, "panel.html", {"details": _distinct(details)})

    pending = PendingQuestions()
    views = Views(templates.env)

    @app.post("/works/{work_id}/questions", response_class=HTMLResponse)
    def ask(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        work_id: int,
        text: Annotated[str, Form()] = "",
        conversation_id: Annotated[str, Form()] = "",
    ) -> Response:
        """Hold the sidebar's Question for its stream; the first starts the Conversation."""
        text = text.strip()
        if not text:
            return Response(status_code=204)

        def refuse(message: str) -> HTMLResponse:
            context = {"text": text, "refused": message}
            return templates.TemplateResponse(request, "conversation/question.html", context)

        new_conversation = not conversation_id
        if new_conversation:
            try:
                conversation_id = api.start_conversation(work_id).id
            except ConflictError as e:
                return refuse(not_embedded(e))
            except NotFoundError as e:
                return refuse(f"Not asked: {e}.")
            except httpx.HTTPError:
                logger.warning("API unreachable", exc_info=True)
                return refuse("Not asked: the API is unreachable.")
            logger.info("Started Conversation %s about Work %d", conversation_id, work_id)
        question = PendingQuestion(work_id=work_id, conversation_id=conversation_id, text=text)
        question_id = pending.put(question)
        logger.info("Question %s waits for its stream", question_id)
        return templates.TemplateResponse(
            request,
            "conversation/question.html",
            {
                "text": text,
                "question_id": question_id,
                "conversation_id": conversation_id,
                "new_conversation": new_conversation,
            },
        )

    @app.get("/questions/{question_id}/stream")
    def stream(question_id: str) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        """The Question's Answer as it streams: the API's events relayed as HTML."""
        events = relay(api, question_id, pending.take(question_id), views)
        return StreamingResponse(
            events, media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app


def _distinct(details: list[CitationDetail]) -> list[CitationDetail]:
    """One entry per Reference: a nested marker can point at the same Reference twice."""
    seen: set[str] = set()
    distinct: list[CitationDetail] = []
    for d in details:
        key = d.reference.key if d.reference else f"unlinked-{d.id}"
        if key not in seen:
            seen.add(key)
            distinct.append(d)
    return distinct


class WebSettings(BaseSettings):
    """Web app configuration from `PE_WEB_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="PE_WEB_", env_file=".env", extra="ignore")

    api_url: str = "http://localhost:8000"


def app_from_env() -> FastAPI:
    """ASGI factory for uvicorn: `uvicorn --factory paperexplorer_web.app:app_from_env`."""
    return create_app(ApiClient.from_url(WebSettings().api_url))
