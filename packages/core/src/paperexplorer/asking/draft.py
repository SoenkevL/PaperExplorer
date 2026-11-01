"""The model's draft as the reader sees it: ended at the first control token, cut at handles.

gemma-4 marks turns and channels with control tokens such as `<|turn>`, `<channel|>` and
`<|tool_response>`. Ollama is meant to keep them out of the text, yet some reached it, followed
by the model's monologue, and the model echoed a `<system>` note it was given (eval of
2026-10-29). So the Answer ends at the first control token, and an echoed `<system>…</system>`
block is left out with the text around it kept. A stop sequence in the Modelfile would not do:
gemma-4 ends its thinking channel with `<channel|>`, so a stop there could cut off the Answer.
"""

import re
from collections.abc import Container

from pydantic import BaseModel

from paperexplorer.asking.handles import GROUP, handle_numbers, unfinished

# gemma-4's control tokens: `<|name>`, `<name|>`, and `<|name|>`.
_CONTROL = re.compile(r"<\|[A-Za-z_][\w-]*\|?>|<[A-Za-z_][\w-]*\|>")
_SYSTEM = re.compile(r"<system>.*?</system>", re.DOTALL)
_OPEN_SYSTEM = "<system>"
# The start of a control token or of a `<system>` tag, which the next delta may finish.
_PARTIAL = re.compile(r"<\|?(?:[A-Za-z_][\w-]*\|?)?\Z")


def answer_draft(draft: str) -> str:
    """The Answer a complete draft holds: before its first control token, without `<system>`."""
    return _kept(draft).strip()


class TextPiece(BaseModel):
    """A run of the draft's text."""

    text: str


class HandlePiece(BaseModel):
    """One handle of the draft, by its number; its Pointer comes with the Answer."""

    handle: int


type DraftPiece = TextPiece | HandlePiece
"""A piece of the draft as it streams: a run of text, or one handle."""


class DraftCut:
    """A draft as it streams, cut as `answer_draft` cuts it and cut at its handles.

    Each delta gives the pieces that can be shown now: runs of text, and one `HandlePiece` per
    number a handle carries (a range gives its two ends). `handles` are the numbers the
    Conversation knows; one it does not know is dropped, as `resolve_answer` drops it.
    `bare_handles` reads a bare `[13]` as `[#13]`, for a Seed that numbers no Reference.
    Text that could still turn into a control token, a `<system>` block or a handle is held
    back until a later delta settles it; once a control token shows, nothing after it is shown.
    """

    def __init__(self, handles: Container[int], *, bare_handles: bool = False) -> None:
        self._handles = handles
        self._bare = bare_handles
        self._draft = ""
        self._shown = 0

    def add(self, delta: str) -> list[DraftPiece]:
        """The pieces of `delta`, and of any text held back before, that can be shown now."""
        self._draft += delta
        kept = _kept(self._draft)
        settled = self._shown + unfinished(kept[self._shown :])
        text, self._shown = kept[self._shown : settled], settled
        pieces: list[DraftPiece] = []
        at = 0
        for m in GROUP.finditer(text):
            if (numbers := handle_numbers(m.group(), bare=self._bare)) is None:
                continue  # the Seed's own marker, such as [74]
            _add_text(pieces, text[at : m.start()])
            pieces += [HandlePiece(handle=n) for n in numbers if n in self._handles]
            at = m.end()
        _add_text(pieces, text[at:])
        return pieces


def _add_text(pieces: list[DraftPiece], text: str) -> None:
    """Add `text` to the pieces, joined to a last piece of text: a dropped handle splits none."""
    if not text:
        return
    if pieces and isinstance(last := pieces[-1], TextPiece):
        last.text += text
    else:
        pieces.append(TextPiece(text=text))


def _kept(draft: str) -> str:
    """The draft's text that stays whatever follows it; it only grows as the draft does."""
    text = _SYSTEM.sub("", draft)
    ends = [m.start() for m in (_CONTROL.search(text), _PARTIAL.search(text)) if m]
    if (opened := text.find(_OPEN_SYSTEM)) >= 0:
        ends.append(opened)  # a block not closed yet, or never
    return text[: min(ends, default=len(text))]
