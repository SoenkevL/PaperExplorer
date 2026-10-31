"""Claude Code UserPromptSubmit hook: point a request for work at CLAUDE.md's Agent skills."""

import json
import re
import sys

# Words that start work a skill covers; whole words only, so "address" is not "add".
_WORK = re.compile(
    r"\b(add|adding|implement\w*|build\w*|feature\w*|support|bug\w*|broken|fail\w*|"
    r"error\w*|crash\w*|slow\w*|regress\w*|refactor\w*|redesign\w*|design\w*|review\w*)\b",
    re.IGNORECASE,
)

NUDGE = (
    "Before the first edit, match this request to a line in CLAUDE.md's `## Agent skills` "
    "and call the Skill tool for it, or offer the user its /command."
)


def nudge(prompt: str) -> str | None:
    """The reminder for a prompt that asks for work a skill covers, else None."""
    return NUDGE if _WORK.search(prompt) else None


def main() -> None:
    """Read the hook's JSON on stdin and print the nudge, which Claude Code adds as context."""
    # Claude Code runs this with the Mac's own python3 (3.10), not the project's 3.14:
    # one except clause and no 3.11+ syntax keep it parseable there.
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    prompt = data.get("prompt") if isinstance(data, dict) else None
    text = nudge(prompt) if isinstance(prompt, str) else None
    if text is not None:
        print(text)


if __name__ == "__main__":
    main()
