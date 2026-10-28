"""Keep the tower's Langfuse traces under their disk cap (PE_LANGFUSE_TRACE_CAP_GB, 6 by default).

Runs on the tower from cron, in the repo, every six hours:
    uv run python scripts/langfuse_prune.py
Measures what ClickHouse and MinIO hold on disk; over the cap, it deletes the oldest tenth of
the traces through the Public API. ClickHouse frees the space after its merges, so the next run
measures again before deleting more. `--dry-run` only measures.
"""

import argparse
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from langfuse.api import LangfuseAPI

from paperexplorer.config import Settings
from paperexplorer.logs import configure_logging
from paperexplorer.tracing.retention import prune

COMPOSE = Path(__file__).resolve().parent.parent / "compose.langfuse.yaml"
# Where each service of compose.langfuse.yaml keeps its share of the trace store.
STORES = {"clickhouse": "/var/lib/clickhouse", "minio": "/data"}

logger = logging.getLogger("langfuse_prune")


def store_bytes() -> int:
    """Bytes the trace store takes on disk, summed over ClickHouse and MinIO."""
    total = 0
    for service, path in STORES.items():
        done = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "exec", "-T", service, "du", "-sk", path],
            capture_output=True,
            text=True,
            check=True,
        )
        total += int(done.stdout.split()[0]) * 1024
    return total


def main() -> int:
    """0 when the store is under its cap or a step of the oldest traces was deleted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="measure only, delete nothing")
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    public_key, secret_key = settings.langfuse_public_key, settings.langfuse_secret_key
    if not (settings.langfuse_base_url and public_key and secret_key):
        logger.error("Set PE_LANGFUSE_BASE_URL and the PE_LANGFUSE_* keys in .env")
        return 1

    size, cap = store_bytes(), int(settings.langfuse_trace_cap_gb * 1e9)
    logger.info("Trace store: %.2f of %.2f GB on disk", size / 1e9, cap / 1e9)
    if args.dry_run:
        return 0
    api = LangfuseAPI(
        base_url=settings.langfuse_base_url,
        username=public_key,
        password=secret_key.get_secret_value(),
    )
    prune(api, size_bytes=size, cap_bytes=cap, now=datetime.now(UTC))
    return 0


if __name__ == "__main__":
    sys.exit(main())
