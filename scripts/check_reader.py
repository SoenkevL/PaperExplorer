"""Check that the running reader links every Citation of a Work and opens a panel for one.

Run against the compose stack: `uv run python scripts/check_reader.py YAAC9XCX`. It prints counts
and ids only, never the Work's text: tower-check.log is public and the Seed's licence unknown.
"""

import argparse
import html
import re
import sys
from collections import Counter

import httpx

from paperexplorer_api.client import ApiClient

_LINK = re.compile(r'class="citation [^"]*"[^>]*hx-get="([^"]+)"')


def main() -> int:
    """0 when every Citation the API has is a link on the page and a panel shows a Reference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zotero_key")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--web", default="http://localhost:8080")
    parser.add_argument("--min-citations", type=int, default=280)
    args = parser.parse_args()

    api = ApiClient(httpx.Client(base_url=args.api, timeout=30, transport=_retrying()))
    work_id = next(w.id for w in api.works() if w.zotero_key == args.zotero_key)
    work = api.work(work_id)
    spans = [c for b in work.blocks for c in b.citations]
    print(f"{args.zotero_key} is Work {work_id}: {len(work.blocks)} blocks, {len(spans)} Citations")
    print(f"stages: {work.stages}")
    print(f"confidence levels: {dict(Counter(c.confidence_level for c in spans))}")

    web = httpx.Client(base_url=args.web, timeout=30, transport=_retrying())
    page = web.get(f"/works/{work_id}").raise_for_status().text
    targets = [html.unescape(u) for u in _LINK.findall(page)]
    linked = {int(c) for t in targets for c in re.findall(r"c=(\d+)", t)}
    print(f"page: {len(targets)} Citation links covering {len(linked)} distinct Citations")
    panel = web.get(targets[0]).raise_for_status().text
    shows_reference = 'class="reference' in panel
    print(f"panel {targets[0]}: {'shows a Reference' if shows_reference else 'shows no Reference'}")

    ok = linked == {c.id for c in spans} and len(spans) >= args.min_citations and shows_reference
    if not ok:
        print(f"FAILED: expected all of >= {args.min_citations} Citations linked and a Reference")
    return 0 if ok else 1


def _retrying() -> httpx.HTTPTransport:
    return httpx.HTTPTransport(retries=5)


if __name__ == "__main__":
    sys.exit(main())
