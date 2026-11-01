"""The handle grammar: how a draft ties its sentences to Chunks, read by Answer and Draft alike.

A handle ties a sentence to a Chunk (design-asking.md, Answers): a group in `[]`, `()`, `{}` or
`<>`, nested one deep (`[<#3><#4>]`), holding only numbers, at least one with a `#`. They stand
apart by commas, semicolons, spaces, middle dots or `#` (`[#5, 6]`, `[#65·66]`, `[#59##60]`). A
range `#5-#7`, any dash, reads as its two ends: handles number Chunks in the order first shown,
not in reading order.
A group without `#`, `[74]` or `(5)`, is the Seed's own marker. A Seed that numbers no Reference
prints no `[74]` of its own, so there a bare group in square brackets, not nested, is a handle
too (`bare`): the model wrote `[13]` for `[#13]`. tests/fixtures/handle-forms.json holds the
forms `resolve_answer` and `DraftCut` are tested with.
"""

import re

_NUMBER = r"#*\d+"
_ITEM = rf"{_NUMBER}(?:[ \t]*[-\u2010-\u2014][ \t]*{_NUMBER})?"
_BRACKETS = {"[": "]", "(": ")", "{": "}", "<": ">"}


def _group(item: str) -> str:
    """A bracketed group of `item`s; without a separator, the next starts with `#` or a bracket."""
    items = rf"(?:{item})(?:(?:[ \t,;·]+|(?=[#\[({{<]))(?:{item}))*"
    return "|".join(
        rf"{re.escape(o)}[ \t]*{items}[ \t]*{re.escape(c)}" for o, c in _BRACKETS.items()
    )


GROUP = re.compile(_group(rf"{_group(_ITEM)}|{_ITEM}"))
"""A bracketed group of numbers: a handle, or the Seed's own marker (`handle_numbers` tells)."""
_NESTED = re.compile(r"[(){}<>\[\]]")
# What a handle is written with; any other character ends the hope that a group is one.
_HANDLE_CHARS = frozenset("#0123456789 \t,;\u00b7-\u2010\u2011\u2012\u2013\u2014[](){}<>")


def handle_numbers(group: str, *, bare: bool) -> list[int] | None:
    """The numbers a `GROUP` match carries as handles, in order; None for the Seed's own marker."""
    if "#" not in group and not (
        bare and group.startswith("[") and not _NESTED.search(group[1:-1])
    ):
        return None
    return [int(n) for n in re.findall(r"\d+", group)]


def unfinished(text: str) -> int:
    """Where a group that more text may finish into a handle starts; else the end of `text`."""
    start = len(text)
    while start > 0 and text[start - 1] in _HANDLE_CHARS:
        start -= 1
    return next(
        (i for i in range(start, len(text)) if text[i] in _BRACKETS and _still_open(text[i:])),
        len(text),
    )


def _still_open(text: str) -> bool:
    """Whether the group `text` opens is open at its end: never closed, nested one deep at most."""
    closing: list[str] = []
    for c in text:
        if c in _BRACKETS:
            if len(closing) == 2:
                return False
            closing.append(_BRACKETS[c])
        elif c in _BRACKETS.values() and (not closing or closing.pop() != c or not closing):
            return False
    return bool(closing)
