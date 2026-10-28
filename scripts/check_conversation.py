"""Ask the running API one Question about a Work over HTTP and report how its stream went.

Run against the compose stack: `uv run python scripts/check_conversation.py YAAC9XCX "<question>"`.
It prints counts, tool names and times only, never the draft's or the Answer's text:
tower-check.log is public and the Seed's licence unknown.
"""

import argparse
import sys
import time
from collections import Counter

import httpx

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import Answer, AnsweredEvent, DeltaEvent, ErrorEvent, StepEvent


def main() -> int:
    """0 when the Question's stream ends with an Answer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zotero_key")
    parser.add_argument("question")
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    api = ApiClient(httpx.Client(base_url=args.api, timeout=30))
    work_id = next((w.id for w in api.works() if w.zotero_key == args.zotero_key), None)
    if work_id is None:
        print(f"FAILED: the API has no Work {args.zotero_key}")
        return 1
    conversation = api.start_conversation(work_id)
    print(f"{args.zotero_key} is Work {work_id}; Conversation {conversation.id}")

    counts: Counter[str] = Counter()
    tools: list[str] = []
    first_delta: float | None = None
    answered: tuple[Answer, float] | None = None
    started = time.monotonic()
    for event in api.ask(conversation.id, args.question):
        counts[event.type] += 1
        match event:
            case StepEvent(tool=tool):
                tools.append(tool)
            case DeltaEvent():
                first_delta = first_delta or time.monotonic() - started
            case AnsweredEvent(answer=answer):
                answered = answer, time.monotonic() - started
            case ErrorEvent(message=message):
                print(f"error after {time.monotonic() - started:.1f} s: {message}")

    print(f"events: {', '.join(f'{name} {n}' for name, n in sorted(counts.items()))}")
    print(f"tools: {' '.join(tools)}")
    print(f"first delta: {f'{first_delta:.1f} s' if first_delta is not None else 'none'}")
    if answered is None:
        print("FAILED: the stream ended without an Answer")
        return 1
    answer, after = answered
    pointers = [p for s in answer.sentences for p in s.pointers]
    print(
        f"answer: {after:.1f} s, {len(answer.text)} characters, {len(answer.sentences)} sentences"
        f" ({sum(bool(s.pointers) for s in answer.sentences)} with a Pointer),"
        f" Pointers close {sum(p.support == 'close' for p in pointers)}"
        f" weak {sum(p.support == 'weak' for p in pointers)},"
        f" Quotes exact {sum(q.exact for q in answer.quotes)}"
        f" not exact {sum(not q.exact for q in answer.quotes)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
