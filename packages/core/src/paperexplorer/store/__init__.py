"""Postgres access: hand-written SQL behind small repositories (ADR 0003)."""

from paperexplorer.store.migrate import migrate

__all__ = ["migrate"]
