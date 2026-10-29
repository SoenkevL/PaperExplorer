"""Ask the running web app one Question about a Work through its sidebar's routes, and report.

Run against the compose stack: `uv run python scripts/check_sidebar.py YAAC9XCX "<question>"`.
It loads the Work's page, posts the Question as the sidebar's form does, reads the stream the
page's EventSource would, and checks that an Answer with at least one Pointer arrives. It prints
counts and times only, never the Answer's text: tower-check.log is public and the Seed's licence
unknown.
"""

import argparse
import html
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator

import httpx

from paperexplorer_api.client import ApiClient

# The model may think for minutes before its first event, and the relay sends nothing meanwhile.
READ_TIMEOUT_S = 300.0


def main() -> int:
    """0 when the sidebar's Question ends in an Answer with at least one Pointer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zotero_key")
    parser.add_argument("question")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--web", default="http://localhost:8080")
    args = parser.parse_args()

    api = ApiClient(httpx.Client(base_url=args.api, timeout=30))
    work_id = next((w.id for w in api.works() if w.zotero_key == args.zotero_key), None)
    if work_id is None:
        print(f"FAILED: the API has no Work {args.zotero_key}")
        return 1
    web = httpx.Client(base_url=args.web, timeout=httpx.Timeout(30, read=READ_TIMEOUT_S))
    page = web.get(f"/works/{work_id}").raise_for_status().text
    sidebar = 'id="conversation"' in page and f'hx-post="/works/{work_id}/questions"' in page
    print(f"{args.zotero_key} is Work {work_id}; page {'has' if sidebar else 'lacks'} the sidebar")
    if not sidebar:
        return 1

    started = time.monotonic()
    data = {"text": args.question, "conversation_id": ""}
    turn = web.post(f"/works/{work_id}/questions", data=data).raise_for_status().text
    stream = re.search(r'sse-connect="([^"]+)"', turn)
    if stream is None:
        refused = re.search(r'class="refused">(.*?)</p>', turn, re.S)
        print(f"FAILED: not asked: {html.unescape(refused[1]) if refused else 'no stream'}")
        return 1
    started_conversation = 'id="conversation-id"' in turn and "hx-swap-oob" in turn
    print(f"posted: {'started a Conversation' if started_conversation else 'no Conversation id'}")

    counts: Counter[str] = Counter()
    first_delta: float | None = None
    last: tuple[str, str] | None = None
    with web.stream("GET", html.unescape(stream[1])) as response:
        response.raise_for_status()
        for name, fragment in _events(response.iter_lines()):
            counts[name] += 1
            if name == "delta" and first_delta is None:
                first_delta = time.monotonic() - started
            last = name, fragment
            if name in ("answer", "error"):
                break
    after = time.monotonic() - started
    print(f"events: {', '.join(f'{name} {n}' for name, n in sorted(counts.items()))}")
    print(f"first delta: {f'{first_delta:.1f} s' if first_delta is not None else 'none'}")
    if last is None or last[0] != "answer":
        message = re.search(r'class="failed">(.*?)</p>', last[1], re.S) if last else None
        print(f"FAILED after {after:.1f} s: no Answer"
              f"{f' ({html.unescape(message[1])})' if message else ''}")  # fmt: skip
        return 1
    answer = last[1]
    supports = Counter(re.findall(r'class="pointer (close|weak)"', answer))
    print(
        f"answer: {after:.1f} s, {answer.count('class="sentence"')} sentences,"
        f" Pointer chips close {supports['close']} weak {supports['weak']},"
        f" Quotes exact {answer.count('class="quote exact"')}"
        f" not exact {answer.count('class="quote not-exact"')}"
    )
    if not supports:
        print("FAILED: the Answer has no Pointer")
        return 1
    return 0


def _events(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Each server-sent event's name and data, as the page's EventSource reads them."""
    name, data = "", list[str]()
    for line in lines:
        if not line:
            if data:
                yield name, "\n".join(data)
            name, data = "", []
            continue
        field, _, value = line.partition(":")
        if field == "event":
            name = value.removeprefix(" ")
        elif field == "data":
            data.append(value.removeprefix(" "))


if __name__ == "__main__":
    sys.exit(main())
