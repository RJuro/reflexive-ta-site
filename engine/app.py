"""Uvicorn entry: `.venv/bin/uvicorn app:app` (no --reload — reload kills 12-min coding jobs)."""
from masshine.api import app  # noqa: F401
