"""Every text of ours the model reads, and the version they make together."""

import hashlib
from importlib import resources

SYSTEM_PROMPT = (resources.files("paperexplorer.asking") / "system-prompt.md").read_text("utf-8")
LAST_REQUEST = (
    "This is your last request for this Question: no tools are left. Answer now from what you"
    " have read, and where it does not settle the Question, say what the Seed does not say."
)
# A Seed that fits is read whole instead of searched (ADR 0010): the eval's baseline, unchanged.
WHOLE_SEED = (
    "For this Question you have no tools, and no search has run: the Seed's whole text is below,"
    " every Chunk under its handle. What is said above about searching and reading with tools"
    " does not apply; read the text below instead. Everything else above does."
)
NO_TOOLS = (
    "You have no tools for this Question: the Seed's whole text is in your instructions. Answer"
    " now from it, and where it does not settle the Question, say what the Seed does not say."
)
# Reading the whole Seed, the request closes with one turn: `NO_TOOLS`, then this, then the
# Question, last. As a turn of its own after the Question, the note was taken for the reader's
# message, and another Question answered (docs/eval-2026-10-31.md).
QUESTION_LABEL = "The reader's Question:"
# An Answer from the whole Seed that gives no handle is asked for them once more.
REDRAFT = (
    "Your Answer above gives no handle. Write it again word for word, and after each sentence"
    " write the handle of the Chunk it rests on, such as [#7], right after its full stop. A"
    " sentence that says what the Seed does not say gets no handle. Change nothing else."
)
# The prompt's version hashes every text of ours the model reads, so an edit cannot forget it.
PROMPT_VERSION = hashlib.sha256(
    "\n\n".join(
        [SYSTEM_PROMPT, LAST_REQUEST, WHOLE_SEED, NO_TOOLS, QUESTION_LABEL, REDRAFT]
    ).encode()
).hexdigest()[:12]
