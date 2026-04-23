"""Compatibility ASGI entrypoint aliases for Uvicorn."""

from .main import app

# Allow both `gov_aggregator.app:app` and `gov_aggregator.app:main`.
main = app

__all__ = ["app", "main"]
