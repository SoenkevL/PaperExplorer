"""What a Jump says: how the Trail names a Work, and why one landed at the top."""

from paperexplorer_api.schemas import WorkBrief, WorkSummary

# Why a Jump shows no place in the Source, by the API's reason.
NO_EVIDENCE = {
    "no_chunks": "This Work has not been cut into Chunks yet, so no place could be looked for.",
    "no_vectors": "This Work has not been embedded yet, so no place could be looked for.",
    "embedder_unreachable": "The Embedder could not be reached, so no place could be looked for.",
    "no_match": "The search found nothing for this sentence.",
}
_LABEL_TITLE_LENGTH = 24


def work_label(work: WorkBrief | WorkSummary) -> str:
    """A Work's short name on the Trail: its first author's surname and year, else its title."""
    if work.creators:
        surname = work.creators[0].split(",")[0].strip()
        return f"{surname} {work.year}" if work.year else surname
    title = work.title or work.zotero_key or "Untitled"
    return title if len(title) <= _LABEL_TITLE_LENGTH else f"{title[:_LABEL_TITLE_LENGTH]}…"
