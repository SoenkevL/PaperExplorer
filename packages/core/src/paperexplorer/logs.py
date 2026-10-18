"""One place to configure stdlib logging for every PaperExplorer process."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import override

from paperexplorer.config import LogFormat, LogLevel


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON line, for container log collectors."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: LogLevel = "INFO", fmt: LogFormat = "text") -> None:
    """Route all `paperexplorer*` loggers to stdout at `level` in the chosen format."""
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
