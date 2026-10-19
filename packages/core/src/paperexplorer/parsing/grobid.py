"""GROBID: where a PDF's headings, paragraphs, sentences, References and Citation markers are.

Only GROBID's structure and coordinates are used; its text is rewritten (ranges expanded,
spaces dropped) and never shown (ADR 0006).
"""

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from pydantic import BaseModel

from paperexplorer.config import Settings
from paperexplorer.parsing.models import BlockKind, Box

logger = logging.getLogger(__name__)

COORDINATE_ELEMENTS = ("head", "p", "s", "ref", "biblStruct", "figure", "formula", "note")
_T = "{http://www.tei-c.org/ns/1.0}"
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d[a-z]?\b")


class GrobidError(Exception):
    """GROBID could not process a PDF."""


class NoTextLayerError(GrobidError):
    """GROBID found no text in the PDF: it is a scan."""


class TeiRef(BaseModel):
    """A Citation marker as GROBID sees it."""

    target: str | None
    text: str
    boxes: list[Box]


class TeiSentence(BaseModel):
    """A sentence and the Citation markers in it."""

    boxes: list[Box]
    refs: list[TeiRef]


class TeiBlock(BaseModel):
    """A heading, paragraph or footnote; `number` is a heading's section number."""

    kind: BlockKind
    number: str | None = None
    boxes: list[Box]
    sentences: list[TeiSentence] = []
    text: str


class TeiReference(BaseModel):
    """A bibliography entry with the fields GROBID parsed from it."""

    id: str
    boxes: list[Box]
    raw_text: str
    surnames: list[str]
    year: str | None
    title: str | None
    doi: str | None


class TeiDocument(BaseModel):
    """GROBID's structure of one PDF."""

    version: str | None
    blocks: list[TeiBlock]
    references: list[TeiReference]


def fetch_tei(pdf: Path, settings: Settings) -> bytes:
    """GROBID's TEI for `pdf`: from the cache, else from the GROBID service (then cached)."""
    content = pdf.read_bytes()
    cache = settings.cache_dir / "grobid" / f"{hashlib.sha256(content).hexdigest()}.tei.xml"
    if cache.exists():
        logger.debug("GROBID cache hit for %s", pdf.name)
        return cache.read_bytes()
    url = settings.grobid_url.rstrip("/") + "/api/processFulltextDocument"
    data: dict[str, str | list[str]] = {
        "consolidateHeader": "0",
        "consolidateCitations": "0",
        "includeRawCitations": "1",
        "segmentSentences": "1",
        "teiCoordinates": list(COORDINATE_ELEMENTS),
    }
    response = _post(url, pdf.name, content, data)
    for attempt in range(4):
        if response.status_code != 503:  # 503: all GROBID workers busy, retry
            break
        time.sleep(2**attempt)
        response = _post(url, pdf.name, content, data)
    if response.status_code == 500 and "NO_BLOCKS" in response.text:
        raise NoTextLayerError(f"GROBID found no text in {pdf.name}")
    if response.status_code != 200:
        raise GrobidError(
            f"GROBID returned {response.status_code} for {pdf.name}: {response.text[:200]}"
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    return response.content


def _post(url: str, name: str, content: bytes, data: dict[str, str | list[str]]) -> httpx.Response:
    return httpx.post(
        url, files={"input": (name, content, "application/pdf")}, data=data, timeout=600
    )


def read_tei(tei: bytes) -> TeiDocument:
    """Blocks in reading order (abstract, body, back matter) and References from GROBID's TEI."""
    root = ET.fromstring(tei)
    parents = {child: parent for parent in root.iter() for child in parent}
    app = root.find(f".//{_T}appInfo/{_T}application")
    blocks: list[TeiBlock] = []
    abstract = root.find(f".//{_T}profileDesc/{_T}abstract")
    if abstract is not None:
        blocks += _blocks(abstract, parents)
    body = root.find(f".//{_T}text/{_T}body")
    if body is not None:
        blocks += _blocks(body, parents)
    back = root.find(f".//{_T}text/{_T}back")
    references: list[TeiReference] = []
    if back is not None:
        for div in back.findall(f"{_T}div"):
            if div.get("type") != "references":
                blocks += _blocks(div, parents)
        bibl = back.findall(f".//{_T}listBibl/{_T}biblStruct")
        references = [_reference(b, i) for i, b in enumerate(bibl)]
    return TeiDocument(
        version=app.get("version") if app is not None else None,
        blocks=blocks,
        references=references,
    )


def _blocks(container: ET.Element, parents: dict[ET.Element, ET.Element]) -> list[TeiBlock]:
    out: list[TeiBlock] = []
    for el in container.iter():
        parent = parents.get(el)
        if el.tag == f"{_T}head" and parent is not None and parent.tag == f"{_T}div":
            boxes = _boxes(el)
            if boxes:
                out.append(
                    TeiBlock(
                        kind=BlockKind.HEADING, number=el.get("n"), boxes=boxes, text=_text(el)
                    )
                )
        elif el.tag == f"{_T}p" and not _inside(parents, el, f"{_T}figure"):
            sentences = [
                TeiSentence(
                    boxes=_boxes(s),
                    refs=[
                        TeiRef(
                            target=(r.get("target") or "").lstrip("#") or None,
                            text=_text(r),
                            boxes=_boxes(r),
                        )
                        for r in s.iter(f"{_T}ref")
                        if r.get("type") == "bibr"
                    ],
                )
                for s in el.findall(f"{_T}s")
            ]
            boxes = [b for s in sentences for b in s.boxes] or _boxes(el)
            if boxes:
                kind = (
                    BlockKind.FOOTNOTE if _inside(parents, el, f"{_T}note") else BlockKind.PARAGRAPH
                )
                out.append(TeiBlock(kind=kind, boxes=boxes, sentences=sentences, text=_text(el)))
        elif el.tag == f"{_T}note" and el.get("place") == "foot" and el.find(f"{_T}p") is None:
            boxes = _boxes(el)
            if boxes:
                out.append(TeiBlock(kind=BlockKind.FOOTNOTE, boxes=boxes, text=_text(el)))
    return out


def _reference(el: ET.Element, index: int) -> TeiReference:
    raw = el.find(f"{_T}note[@type='raw_reference']")
    raw_text = _text(raw) if raw is not None else _text(el)
    authors = el.findall(f"{_T}analytic/{_T}author") or el.findall(f"{_T}monogr/{_T}author")
    surnames = [_text(s) for a in authors if (s := a.find(f".//{_T}surname")) is not None]
    title = el.find(f"{_T}analytic/{_T}title")
    if title is None:
        title = el.find(f"{_T}monogr/{_T}title")
    doi = el.find(f".//{_T}idno[@type='DOI']")
    year = _YEAR.search(raw_text)
    return TeiReference(
        id=el.get(_XML_ID) or f"b{index}",
        boxes=_boxes(el),
        raw_text=raw_text,
        surnames=surnames,
        year=year.group(0) if year else None,
        title=_text(title) if title is not None and _text(title) else None,
        doi=_text(doi) if doi is not None else None,
    )


def _boxes(el: ET.Element) -> list[Box]:
    out: list[Box] = []
    for part in (el.get("coords") or "").split(";"):
        p = part.split(",")
        if len(p) >= 5:
            out.append(
                Box(
                    page=int(p[0]),
                    x=float(p[1]),
                    y=float(p[2]),
                    width=float(p[3]),
                    height=float(p[4]),
                )
            )
    return out


def _text(el: ET.Element) -> str:
    return re.sub(r"\s+", " ", " ".join(el.itertext())).strip()


def _inside(parents: dict[ET.Element, ET.Element], el: ET.Element, tag: str) -> bool:
    node = parents.get(el)
    while node is not None:
        if node.tag == tag:
            return True
        node = parents.get(node)
    return False
