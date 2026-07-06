"""MASSHINE engine — modular package.

Compatibility façade: everything the tools and tests reach for via `import masshine as m` is
re-exported here, so the split into submodules is transparent to callers. The pipeline spine is:
  ingest (structure + sentence index) → coding (blind, parallel; single/critic/panel)
  → reconcile (stable-id codebook) → friction (pure Python) → themes (sequential, grounded walk)
  → db/export. `runner` orchestrates resumable runs; `packs` loads standpoint lenses.
"""
from __future__ import annotations

from . import llm  # noqa: F401  (m.llm.usage(), m.llm.model(), monkeypatch target)
from .config import CONCURRENCY, DATA_DIR, DB_PATH, EXPORT_DIR, PROMPTS, ROOT  # noqa: F401
from .ingest import (  # noqa: F401
    _line_offsets, _numbered, _slug, ingest, sentence_index, structure,
)
from .coding import (  # noqa: F401
    _apply_critic, _norm_type, _parse_codes, _section_block, code_document,
    code_sections, code_sections_compare, code_sections_panel,
)
from .reconcile import (  # noqa: F401
    _next_code_id, _reconcile_messages, _write_codebook, load_codebook,
    reconcile, reconcile_into, reconcile_project,
)
from .friction import friction  # noqa: F401
from .themes import (  # noqa: F401
    _codebook_listing, _doc_transcript_block, _prior_themes_block, _resolve_step_themes,
    _theorist_codes_block, theorize, theorize_panel, theorize_panel_sequential,
    theorize_project, theorize_project_sequential, theorize_walk,
    transcript_block_from_sentences,
)
from .db import export_json, init_db, new_run, resolve, resolve_ev  # noqa: F401
from .runner import (  # noqa: F401
    add_document, demo, ensure_coded_panel, ensure_coded_standard,
    load_checkpoint, save_checkpoint,
)
from . import packs, render_md  # noqa: F401


def model() -> str:  # some callers use m.model() directly
    return llm.model()
