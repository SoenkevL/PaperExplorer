# pyright: reportMissingTypeStubs=false
# pypdfium2 ships no type stubs; this module is the only one that touches it.
"""The PDF's own text layer, read with pdfium: characters as printed, and internal links."""

import bisect
import ctypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from paperexplorer.parsing.models import Box

_TOLERANCE = 0.8  # points a character's centre may lie outside a GROBID box


@dataclass(frozen=True, slots=True)
class Glyph:
    """One character of a page's text layer; `x`, `y` is its centre, top-left origin."""

    page: int
    index: int
    char: str
    x: float
    y: float
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
        for i in range(len(self._pdf)):
            self.links += _links(self._pdf, i, self.page_sizes)

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
                if prev is None:
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
        return Reading("".join(chars), sources)


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
