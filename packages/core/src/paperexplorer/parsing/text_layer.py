# pyright: reportMissingTypeStubs=false
# pypdfium2 ships no type stubs; this module is the only one that touches it.
"""The PDF's own text layer, read with pdfium: characters as printed, and internal links."""

import bisect
import ctypes
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from paperexplorer.parsing.models import Box

_TOLERANCE = 0.8  # points a character's centre may lie outside a GROBID box
_SCAN_MAX_GLYPHS = 20  # a page with fewer printed characters has no real text layer
_SCAN_MIN_IMAGE = 0.7  # ...and is a scan when one image covers this share of it
# How text layers encode a hyphen that only breaks a word across lines.
_SOFT_HYPHENS = frozenset("\ufffe\u00ad\u0002")
# TeX fonts print accents as separate spacing glyphs before the letter; map each to its
# combining mark.
_COMBINING = {
    "\u00b4": "\u0301",  # acute
    "\u0060": "\u0300",  # grave
    "\u02c6": "\u0302",  # circumflex
    "\u02dc": "\u0303",  # tilde
    "\u00a8": "\u0308",  # diaeresis
    "\u00b8": "\u0327",  # cedilla
    "\u02c7": "\u030c",  # caron
    "\u02d8": "\u0306",  # breve
    "\u02d9": "\u0307",  # dot above
    "\u02da": "\u030a",  # ring above
    "\u02dd": "\u030b",  # double acute
}


@dataclass(frozen=True, slots=True)
class Glyph:
    """One character of a page's text layer; `x`, `y` is its centre, top-left origin."""

    page: int
    index: int
    char: str
    x: float
    y: float
    height: float
    generated: bool
    hyphen: bool
    map_error: bool


@dataclass(frozen=True, slots=True)
class Reading:
    """Text read under some boxes; `sources[i]` is the glyph behind `text[i]`, None for a space."""

    text: str
    sources: list[Glyph | None]


@dataclass(frozen=True, slots=True)
class InternalLink:
    """A link annotation pointing inside the PDF; target x and y are None for a page-only target."""

    source: Box
    target_page: int
    target_x: float | None
    target_y: float | None


class TextLayer:
    """All characters of a PDF's text layer, by page, and its internal links."""

    def __init__(self, pdf: Path) -> None:
        self._pdf = pdfium.PdfDocument(pdf)
        self.page_sizes: dict[int, tuple[float, float]] = {}
        self.glyphs: dict[int, list[Glyph]] = {}
        self._by_y: dict[int, tuple[list[float], list[Glyph]]] = {}
        for i in range(len(self._pdf)):
            page = self._pdf[i]
            width, height = page.get_width(), page.get_height()
            self.page_sizes[i + 1] = (width, height)
            self.glyphs[i + 1] = _glyphs(page, i + 1, height)
            printed = sorted(
                (g for g in self.glyphs[i + 1] if not g.generated and g.char.strip()),
                key=lambda g: g.y,
            )
            self._by_y[i + 1] = ([g.y for g in printed], printed)
        self.links: list[InternalLink] = []
        self._scanned_pages = 0
        for i in range(len(self._pdf)):
            self.links += _links(self._pdf, i, self.page_sizes)
            if (
                len(self._by_y[i + 1][1]) < _SCAN_MAX_GLYPHS
                and _image_cover(self._pdf[i]) >= _SCAN_MIN_IMAGE
            ):
                self._scanned_pages += 1

    def is_scan(self) -> bool:
        """Whether most pages are images without a text layer, so only OCR can read them."""
        return self._scanned_pages * 2 >= max(len(self.page_sizes), 1)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._pdf.close()

    def read(self, boxes: Sequence[Box]) -> Reading:
        """The characters whose centres lie inside `boxes`, box by box, as printed.

        Spaces come from the text layer (real or generated); a line-end dash is dropped only
        when pdfium marks it as a soft hyphen.
        """
        chars: list[str] = []
        sources: list[Glyph | None] = []
        seen: set[tuple[int, int]] = set()
        soft = False  # the last glyph was a soft hyphen: join the next one directly
        for box in boxes:
            page = self.glyphs.get(box.page, [])
            ys, printed = self._by_y.get(box.page, ([], []))
            band = printed[
                bisect.bisect_left(ys, box.y - _TOLERANCE) : bisect.bisect_right(
                    ys, box.y + box.height + _TOLERANCE
                )
            ]
            inside = sorted(
                (g for g in band if (g.page, g.index) not in seen and _contains(box, g)),
                key=lambda g: g.index,
            )
            prev: Glyph | None = None
            for g in inside:
                seen.add((g.page, g.index))
                if g.char in _SOFT_HYPHENS:
                    soft = True
                    continue
                if soft:
                    soft = False
                elif prev is None:
                    if chars and chars[-1] != " ":
                        last = sources[-1]
                        if chars[-1] == "-" and last is not None and last.hyphen:
                            chars.pop()
                            sources.pop()
                        else:
                            chars.append(" ")
                            sources.append(None)
                elif chars[-1] != " " and any(
                    b.char.isspace() or b.generated for b in page[prev.index + 1 : g.index]
                ):
                    chars.append(" ")
                    sources.append(None)
                chars.append(g.char)
                sources.append(g)
                prev = g
        _compose_accents(chars, sources)
        return Reading("".join(chars), sources)


def _compose_accents(chars: list[str], sources: list[Glyph | None]) -> None:
    """Merge each spacing accent into the letter it sits on (next letter, else previous one)."""
    i = 0
    while i < len(chars):
        mark = _COMBINING.get(chars[i])
        if mark is None:
            i += 1
            continue
        for j in (i + 1, i - 1):
            if 0 <= j < len(chars) and chars[j].isalpha():
                base = "i" if chars[j] == "\u0131" else chars[j]  # TeX's dotless i takes the accent
                composed = unicodedata.normalize("NFC", base + mark)
                if len(composed) == 1:
                    chars[j] = composed
                    del chars[i]
                    del sources[i]
                    break
        else:
            i += 1


def _image_cover(page: pdfium.PdfPage) -> float:
    """The share of the page covered by its largest image."""
    width, height = page.get_width(), page.get_height()
    largest = 0.0
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    for i in range(raw.FPDFPage_CountObjects(page.raw)):
        obj = raw.FPDFPage_GetObject(page.raw, i)
        if raw.FPDFPageObj_GetType(obj) != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        raw.FPDFPageObj_GetBounds(
            obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)
        )
        largest = max(largest, (right.value - left.value) * (top.value - bottom.value))
    return largest / (width * height) if width and height else 0.0


def _loose_height(tp: pdfium.PdfTextPage, index: int, rect: Any) -> float:
    """The glyph's printed height, ascent to descent: its font size as rendered."""
    raw.FPDFText_GetLooseCharBox(tp.raw, index, ctypes.byref(rect))
    return float(rect.top - rect.bottom)


def _contains(box: Box, g: Glyph) -> bool:
    return (
        box.x - _TOLERANCE <= g.x <= box.x + box.width + _TOLERANCE
        and box.y - _TOLERANCE <= g.y <= box.y + box.height + _TOLERANCE
    )


def _glyphs(page: pdfium.PdfPage, number: int, height: float) -> list[Glyph]:
    tp = page.get_textpage()
    n: int = raw.FPDFText_CountChars(tp.raw)
    units: list[int] = [raw.FPDFText_GetUnicode(tp.raw, i) for i in range(n)]
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    loose = raw.FS_RECTF()
    out: list[Glyph] = []
    for i in range(n):
        u = units[i]
        # pdfium returns UTF-16 units: join surrogate pairs (maths letters), blank the low half.
        if 0xD800 <= u <= 0xDBFF and i + 1 < n and 0xDC00 <= units[i + 1] <= 0xDFFF:
            u = 0x10000 + ((u - 0xD800) << 10) + (units[i + 1] - 0xDC00)
        elif 0xDC00 <= u <= 0xDFFF:
            u = 0
        raw.FPDFText_GetCharBox(
            tp.raw,
            i,
            ctypes.byref(left),
            ctypes.byref(right),
            ctypes.byref(bottom),
            ctypes.byref(top),
        )
        out.append(
            Glyph(
                number,
                i,
                chr(u) if u else "",
                (left.value + right.value) / 2,
                height - (bottom.value + top.value) / 2,
                _loose_height(tp, i, loose),
                bool(raw.FPDFText_IsGenerated(tp.raw, i)),
                bool(raw.FPDFText_IsHyphen(tp.raw, i)),
                bool(raw.FPDFText_HasUnicodeMapError(tp.raw, i)),
            )
        )
    tp.close()
    return out


def _links(
    pdf: pdfium.PdfDocument, index: int, sizes: dict[int, tuple[float, float]]
) -> list[InternalLink]:
    page = pdf[index]
    # pypdfium2 leaves the document handle's type unannotated.
    doc: Any = pdf.raw  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    height = sizes[index + 1][1]
    out: list[InternalLink] = []
    pos = ctypes.c_int(0)
    link = raw.FPDF_LINK()
    rect = raw.FS_RECTF()
    x, y, zoom = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    has_x, has_y, has_zoom = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    while raw.FPDFLink_Enumerate(page.raw, ctypes.byref(pos), ctypes.byref(link)):
        dest = raw.FPDFLink_GetDest(doc, link)
        if not dest:
            action = raw.FPDFLink_GetAction(link)
            if not action or raw.FPDFAction_GetType(action) != raw.PDFACTION_GOTO:
                continue  # external URI or other action
            dest = raw.FPDFAction_GetDest(doc, action)
            if not dest:
                continue
        target = raw.FPDFDest_GetDestPageIndex(doc, dest) + 1
        if target not in sizes:
            continue
        raw.FPDFDest_GetLocationInPage(
            dest,
            ctypes.byref(has_x),
            ctypes.byref(has_y),
            ctypes.byref(has_zoom),
            ctypes.byref(x),
            ctypes.byref(y),
            ctypes.byref(zoom),
        )
        raw.FPDFLink_GetAnnotRect(link, ctypes.byref(rect))
        # Some producers (Springer Nature, Taylor & Francis) store rects upside down.
        left, right = sorted((rect.left, rect.right))
        bottom, top = sorted((rect.bottom, rect.top))
        exact = bool(has_x.value) and bool(has_y.value)
        out.append(
            InternalLink(
                source=Box(
                    page=index + 1, x=left, y=height - top, width=right - left, height=top - bottom
                ),
                target_page=target,
                target_x=x.value if exact else None,
                target_y=sizes[target][1] - y.value if exact else None,
            )
        )
    return out
