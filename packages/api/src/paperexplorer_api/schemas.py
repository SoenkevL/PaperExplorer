"""Response models: the API contract every frontend builds on."""

from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    """Liveness of the API process."""

    status: Literal["ok"] = "ok"
    version: str
