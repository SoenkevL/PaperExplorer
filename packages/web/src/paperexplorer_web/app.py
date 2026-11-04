"""FastAPI + Jinja application factory for the web app."""

import hashlib
import logging
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from paperexplorer_api.client import ApiClient, ConflictError, NotFoundError
from paperexplorer_api.schemas import (
    Block,
    CheckCost,
    CitationDetail,
    DuplicateItems,
    Merge,
    ReadersChoice,
    Resolution,
    WorkBrief,
    WorkSummary,
)
from paperexplorer_web.checks import PendingCheck, relay_check
from paperexplorer_web.library import LibraryList, library_list, row
from paperexplorer_web.relay import Pending, PendingQuestion, Views, not_embedded, relay
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
# Where a Resolution's Work was found, by the layer that matched it.
SOURCES = {
    "library": "in your Library",
    "crossref": "found in Crossref",
    "openalex": "found in OpenAlex",
    "reader": "your choice",
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


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _usd(amount: float) -> str:
    # Cents at least, and the fractions of a cent a single OpenAlex call costs
    whole, _, cents = f"{amount:.4f}".rstrip("0").partition(".")
    return f"{whole}.{cents.ljust(2, '0')}"


def _static_url(path: str) -> str:
    # The contents' hash in the URL: a deploy that changes a file changes its URL, so a browser
    # never runs a cached old script beside a new page. Hashed on every render (a few files, a few
    # hundred kB), so a dev server sees an edited file without a restart.
    version = hashlib.sha256((_HERE / "static" / path).read_bytes()).hexdigest()[:12]
    return f"/static/{path}?v={version}"


def templates() -> Jinja2Templates:
    """The web app's Jinja templates (autoescaped), with the reader's helpers."""
    loaded = Jinja2Templates(directory=_HERE / "templates")
    loaded.env.globals["panel_url"] = _panel_url  # pyright: ignore[reportUnknownMemberType]
    loaded.env.globals["evidence"] = EVIDENCE  # pyright: ignore[reportUnknownMemberType]
    loaded.env.globals["sources"] = SOURCES  # pyright: ignore[reportUnknownMemberType]
    loaded.env.globals["static_url"] = _static_url  # pyright: ignore[reportUnknownMemberType]
    loaded.env.globals["plural"] = _plural  # pyright: ignore[reportUnknownMemberType]
    loaded.env.globals["usd"] = _usd  # pyright: ignore[reportUnknownMemberType]
    return loaded


def create_app(api: ApiClient) -> FastAPI:
    """Build the web app on top of an API client."""
    app = FastAPI(title="PaperExplorer")
    pages = templates()
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    def not_found(request: Request, message: str) -> HTMLResponse:
        return pages.TemplateResponse(
            request, "not_found.html", {"message": message}, status_code=404
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        works: list[WorkSummary] = []
        library: LibraryList | None = None
        has_library = False
        message: str | None = None
        try:
            api_version: str | None = api.health().version
        except httpx.HTTPError:
            logger.warning("API unreachable", exc_info=True)
            api_version = None
        if api_version is not None:
            try:
                library = library_list(api.library(), "", 0)
                has_library = True
            except NotFoundError:
                works = _works_or_none()
            except httpx.HTTPError as e:
                logger.warning("The Library could not be read", exc_info=True)
                has_library = True
                message = f"The Library could not be read: {_why(e)}"
        context = {
            "api_version": api_version,
            "works": works,
            "library": library,
            "has_library": has_library,
            "message": message,
        }
        return pages.TemplateResponse(request, "index.html", context)

    def _works_or_none() -> list[WorkSummary]:
        try:
            return api.works()
        except httpx.HTTPError:
            logger.warning("Works could not be read", exc_info=True)
            return []

    @app.get("/library", response_class=HTMLResponse)
    def library_search(  # pyright: ignore[reportUnusedFunction]
        request: Request, q: str = "", offset: Annotated[int, Query(ge=0)] = 0
    ) -> HTMLResponse:
        """The list for a search; a later page's rows alone, to go after those shown."""
        try:
            library = library_list(api.library(q, offset=offset), q, offset)
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("The Library could not be read", exc_info=True)
            message = f"The Library could not be read: {_why(e)}"
            template = "library_more_failed.html" if offset else "library_list.html"
            return pages.TemplateResponse(request, template, {"library": None, "message": message})
        template = "library_rows.html" if offset else "library_list.html"
        return pages.TemplateResponse(request, template, {"library": library, "message": None})

    @app.get("/duplicates", response_class=HTMLResponse)
    def duplicates(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """The Duplicate Items as they stand after a sync."""
        return duplicates_view(request, duplicate_items(), "", failed=False)

    @app.post("/library/sync", response_class=HTMLResponse)
    def library_sync(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """Bring the Library up to date with Zotero; the page then reads what it shows again."""
        try:
            synced = api.sync_library()
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("The Library was not synced", exc_info=True)
            message = f"The Library was not synced: {_why(e)}"
            return pages.TemplateResponse(request, "library_sync_status.html", {"message": message})
        message = (
            "Zotero could not be reached; this is the last copy of the Library."
            if synced.stale
            else None
        )
        return pages.TemplateResponse(
            request,
            "library_sync_status.html",
            {"message": message},
            headers={"HX-Trigger": "library-synced"},
        )

    def row_view(request: Request, work_id: int, message: str | None = None) -> HTMLResponse:
        """A Work's row as it now stands; `message` says why when an action was refused."""
        lost = {"work_id": work_id, "message": message}
        try:
            work = api.work_stages(work_id)
        except NotFoundError:
            if message is None:
                return HTMLResponse("")
            return pages.TemplateResponse(request, "library_row_lost.html", lost)
        except httpx.HTTPError:
            logger.warning("Work %d's Stages could not be read", work_id, exc_info=True)
            return pages.TemplateResponse(request, "library_row_lost.html", lost)
        return pages.TemplateResponse(request, "library_row.html", {"row": row(work, message)})

    @app.get("/works/{work_id}/row", response_class=HTMLResponse)
    def work_row(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """A Work's row, as polled while a Stage Run holds it."""
        return row_view(request, work_id)

    @app.post("/works/{work_id}/ingesting", response_class=HTMLResponse)
    def ingest(  # pyright: ignore[reportUnusedFunction]
        request: Request, work_id: int, from_stage: Annotated[str | None, Form()] = None
    ) -> HTMLResponse:
        """Start Ingesting a Work, from a Stage the reader confirmed or where it stopped."""
        try:
            work = api.ingest(work_id, from_stage=from_stage)
        except (httpx.HTTPError, ConflictError, NotFoundError) as e:
            logger.warning("Work %d not started", work_id, exc_info=True)
            return row_view(request, work_id, f"Not started: {_why(e)}")
        return pages.TemplateResponse(request, "library_row.html", {"row": row(work)})

    @app.delete("/works/{work_id}/stage-run", response_class=HTMLResponse)
    def cancel_stage_run(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """Cancel a Work's waiting Stage Run."""
        try:
            api.cancel_stage_run(work_id)
        except (httpx.HTTPError, ConflictError, NotFoundError) as e:
            logger.warning("Work %d not cancelled", work_id, exc_info=True)
            return row_view(request, work_id, f"Not cancelled: {_why(e)}")
        return row_view(request, work_id)

    @app.post("/works/{work_id}/resolving", response_class=HTMLResponse)
    def resolve_references(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """Start the Stages that resolve an Ingested Work's References."""
        try:
            work = api.resolve_references(work_id)
        except (httpx.HTTPError, ConflictError, NotFoundError) as e:
            logger.warning("Work %d's References not started", work_id, exc_info=True)
            return row_view(request, work_id, f"Not started: {_why(e)}")
        return pages.TemplateResponse(request, "library_row.html", {"row": row(work)})

    def duplicate_items() -> list[DuplicateItems]:
        """Each DOI's Duplicate Items; none when the API serves none (no Zotero keys) or is down."""
        try:
            return api.duplicates()
        except NotFoundError:
            return []
        except httpx.HTTPError:
            logger.warning("Duplicate Items could not be read", exc_info=True)
            return []

    def duplicates_view(
        request: Request, groups: list[DuplicateItems], message: str, *, failed: bool
    ) -> HTMLResponse:
        context = {"groups": groups, "message": message, "failed": failed}
        return pages.TemplateResponse(request, "duplicates.html", context)

    @app.put("/duplicates/{key}/stand", response_class=HTMLResponse)
    def let_stand(request: Request, key: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """Let this Duplicate Item stand for its DOI."""
        try:
            groups = api.let_stand(key)
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("%s not let stand", key, exc_info=True)
            return duplicates_view(
                request, duplicate_items(), f"Not let stand: {_why(e)}", failed=True
            )
        return duplicates_view(request, groups, f"{key} now stands for its DOI.", failed=False)

    @app.post("/duplicates/merge", response_class=HTMLResponse)
    def merge(  # pyright: ignore[reportUnusedFunction]
        request: Request, keep: Annotated[str, Form()], other: Annotated[str, Form()]
    ) -> HTMLResponse:
        """Merge `other` into `keep` in Zotero; the page asked the reader first."""
        try:
            moved = api.merge(Merge(keep=keep, other=other)).moved
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("%s not merged into %s", other, keep, exc_info=True)
            return duplicates_view(
                request, duplicate_items(), f"Not merged: {_why(e)}", failed=True
            )
        logger.info("Merged %s into %s; %d Resolutions moved", other, keep, moved)
        message = (
            f"Merged {other} into {keep}: {other} is in Zotero's trash,"
            f" {_plural(moved, 'Resolution', 'Resolutions')} moved."
        )
        return duplicates_view(request, duplicate_items(), message, failed=False)

    @app.get("/works/{work_id}", response_class=HTMLResponse)
    def work(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            text = api.work(work_id)
        except NotFoundError as e:
            return not_found(request, str(e))
        return pages.TemplateResponse(
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
        return pages.TemplateResponse(request, "panel.html", {"details": _distinct(details)})

    def resolution(
        request: Request,
        work_id: int,
        key: str,
        citation_id: int,
        resolution: Resolution | None,
        message: str | None = None,
    ) -> HTMLResponse:
        context = {
            "work_id": work_id,
            "citation_id": citation_id,
            "key": key,
            "res": resolution,
            "message": message,
        }
        return pages.TemplateResponse(request, "resolution.html", context)

    def as_it_stands(citation_id: int) -> Resolution | None:
        """The Reference's Resolution as the API holds it, read through its Citation."""
        try:
            reference = api.citation(citation_id).reference
        except httpx.HTTPError, NotFoundError:
            logger.warning("Citation %d could not be read again", citation_id, exc_info=True)
            return None
        return reference.resolution if reference else None

    @app.post("/works/{work_id}/references/{key}/check", response_class=HTMLResponse)
    def check_reference(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        work_id: int,
        key: str,
        c: Annotated[int, Form()],
        refresh: Annotated[bool, Form()] = False,
    ) -> HTMLResponse:
        """Check one Reference ("Check again" past the cache): its Resolution, or why not."""
        try:
            checked = api.check_reference(work_id, key, refresh=refresh)
        except NotFoundError as e:
            message = _unserved("Not checked", e)
            return resolution(request, work_id, key, c, as_it_stands(c), message)
        except httpx.HTTPError as e:
            logger.warning("Check of Reference %s failed", key, exc_info=True)
            return resolution(request, work_id, key, c, as_it_stands(c), f"Not checked: {e}.")
        if checked.resolution is None:
            return resolution(request, work_id, key, c, as_it_stands(c), checked.stopped)
        return resolution(request, work_id, key, c, checked.resolution)

    @app.get("/works/{work_id}/references/{key}/candidates", response_class=HTMLResponse)
    def candidates(  # pyright: ignore[reportUnusedFunction]
        request: Request, work_id: int, key: str, c: int
    ) -> HTMLResponse:
        """The Works the reader might choose for a Reference, each with its button."""
        found: list[WorkBrief] = []
        message: str | None = None
        try:
            found = api.candidates(work_id, key)
        except NotFoundError as e:
            message = _unserved("No candidates", e)
        except httpx.HTTPError as e:
            logger.warning("Candidates of Reference %s failed", key, exc_info=True)
            message = f"No candidates: {e}."
        context = {
            "work_id": work_id,
            "citation_id": c,
            "key": key,
            "candidates": found,
            "message": message,
        }
        return pages.TemplateResponse(request, "candidates.html", context)

    @app.put("/works/{work_id}/references/{key}/choice", response_class=HTMLResponse)
    def choose(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        work_id: int,
        key: str,
        c: Annotated[int, Form()],
        pick: Annotated[Literal["work", "named", "none"], Form()],
        work_id_: Annotated[int | None, Form(alias="work_id")] = None,
        named: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        """Set the Reader's Choice: a candidate, a Zotero key or DOI, or none."""
        match pick:
            case "work":
                choice = ReadersChoice(work_id=work_id_)
            case "named" if named.strip():
                choice = ReadersChoice(key=named.strip())
            case "named":
                return resolution(
                    request, work_id, key, c, as_it_stands(c), "Name a Zotero key or a DOI."
                )
            case "none":
                choice = ReadersChoice()
        try:
            return resolution(request, work_id, key, c, api.choose(work_id, key, choice))
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("Reader's Choice for Reference %s refused", key, exc_info=True)
            return resolution(request, work_id, key, c, as_it_stands(c), f"Not chosen: {_why(e)}")

    @app.delete("/works/{work_id}/references/{key}/choice", response_class=HTMLResponse)
    def clear_choice(  # pyright: ignore[reportUnusedFunction]
        request: Request, work_id: int, key: str, c: int
    ) -> HTMLResponse:
        """Clear the Reader's Choice: the Reference resolved again."""
        try:
            return resolution(request, work_id, key, c, api.clear_choice(work_id, key))
        except (httpx.HTTPError, NotFoundError) as e:
            logger.warning("Reader's Choice for Reference %s not cleared", key, exc_info=True)
            return resolution(request, work_id, key, c, as_it_stands(c), f"Not cleared: {_why(e)}")

    @app.get("/works/{work_id}/check", response_class=HTMLResponse)
    def check_box(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """The Work page's Registry Check: what it would ask, then the button that starts it."""
        cost: CheckCost | None = None
        message: str | None = None
        try:
            cost = api.check_cost(work_id)
        except NotFoundError as e:
            message = f"No Registry Checks: the API serves none ({e}); they need Zotero's keys."
        except httpx.HTTPError:
            logger.warning("Check cost of Work %d failed", work_id, exc_info=True)
            message = "No Registry Checks: the API is unreachable."
        context = {"work_id": work_id, "cost": cost, "message": message}
        return pages.TemplateResponse(request, "check.html", context)

    checks: Pending[PendingCheck] = Pending()

    @app.post("/works/{work_id}/checks", response_class=HTMLResponse)
    def start_check(request: Request, work_id: int) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        """Hold the Check for the page's stream, which starts it."""
        check_id = checks.put(PendingCheck(work_id=work_id))
        logger.info("Check %s of Work %d waits for its stream", check_id, work_id)
        return pages.TemplateResponse(request, "check_running.html", {"check_id": check_id})

    @app.get("/checks/{check_id}/stream")
    def check_stream(check_id: str) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        """The Check as it runs: each Reference's Resolution, then how it ended."""
        events = relay_check(api, check_id, checks.take(check_id), pages.env)
        return StreamingResponse(
            events, media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    pending: Pending[PendingQuestion] = Pending()
    views = Views(pages.env)

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
            return pages.TemplateResponse(request, "conversation/question.html", context)

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
        return pages.TemplateResponse(
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


def _unserved(outcome: str, error: NotFoundError) -> str:
    """What the reader hears when the API has no such route or thing (404)."""
    return (
        f"{outcome}: the API does not serve this ({error}); Checks, choices and Duplicate"
        " Items need Zotero's keys."
    )


def _why(error: httpx.HTTPError | NotFoundError | ConflictError) -> str:
    """The API's own reason for refusing, when it gave one."""
    if isinstance(error, httpx.HTTPStatusError):
        try:
            return f"{error.response.json()['detail']}."
        except ValueError, KeyError, TypeError:
            return f"the API answered {error.response.status_code}."
    return f"{error}."


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
