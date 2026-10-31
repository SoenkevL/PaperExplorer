"""Where the Answer ends in the model's draft: at the chat template's first control token.

gemma-4 marks turns and channels with control tokens such as `<|turn>`, `<channel|>` and
`<|tool_response>`. Ollama is meant to keep them out of the text, yet some reached it, followed
by the model's monologue, and the model echoed a `<system>` note it was given (eval of
2026-10-29). So the Answer ends at the first control token, and an echoed `<system>…</system>`
block is left out with the text around it kept. A stop sequence in the Modelfile would not do:
gemma-4 ends its thinking channel with `<channel|>`, so a stop there could cut off the Answer.
"""

import re

# gemma-4's control tokens: `<|name>`, `<name|>`, and `<|name|>`.
_CONTROL = re.compile(r"<\|[A-Za-z_][\w-]*\|?>|<[A-Za-z_][\w-]*\|>")
_SYSTEM = re.compile(r"<system>.*?</system>", re.DOTALL)
_OPEN_SYSTEM = "<system>"
# The start of a control token or of a `<system>` tag, which the next delta may finish.
_PARTIAL = re.compile(r"<\|?(?:[A-Za-z_][\w-]*\|?)?\Z")


def answer_draft(draft: str) -> str:
    """The Answer a complete draft holds: before its first control token, without `<system>`."""
    return _kept(draft).strip()


class DraftCut:
    """A draft as it streams, cut as `answer_draft` cuts it: what each delta adds to show.

    Text that could still turn into a control token or a `<system>` block is held back until a
    later delta settles it; once a control token shows, nothing after it is shown.
    """

    def __init__(self) -> None:
        self._draft = ""
        self._shown = 0

    def add(self, delta: str) -> str:
        """The text of `delta`, and of any held back before, that can be shown now."""
        self._draft += delta
        kept = _kept(self._draft)
        shown, self._shown = kept[self._shown :], len(kept)
        return shown


def _kept(draft: str) -> str:
    """The draft's text that stays whatever follows it; it only grows as the draft does."""
    text = _SYSTEM.sub("", draft)
    ends = [m.start() for m in (_CONTROL.search(text), _PARTIAL.search(text)) if m]
    if (opened := text.find(_OPEN_SYSTEM)) >= 0:
        ends.append(opened)  # a block not closed yet, or never
    return text[: min(ends, default=len(text))]
