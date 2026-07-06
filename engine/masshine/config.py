"""Shared paths and knobs for the engine.

ROOT is the ENGINE directory. The package lives at engine/masshine/, so parent.parent is
engine/ — identical to the old monolith's `Path(__file__).resolve().parent`. Tools rely on
this (e.g. `ROOT.parent / "transcripts_sample"`), so it must not drift.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "masshine.db"
EXPORT_DIR = ROOT / "exports"
PROMPTS = ROOT / "prompts"
DATA_DIR = Path(os.environ.get("MASSHINE_DATA_DIR", ROOT / "data"))  # projects live here (Phase 3)
CONCURRENCY = int(os.environ.get("MASSHINE_CONCURRENCY", "8"))  # parallel section-coder calls
