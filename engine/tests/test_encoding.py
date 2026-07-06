"""P1.1 — encoding-aware source reading (F1). Windows-1252 transcripts (curly apostrophes at
0x92) must not become mojibake; read_source falls back utf-8 → cp1252 → replace, then
normalizes to NFC. All offline (no LLM involved — this is pure file I/O)."""
from __future__ import annotations

from masshine.ingest import read_source


def test_read_source_valid_utf8_passthrough(tmp_path):
    p = tmp_path / "utf8.txt"
    p.write_text("Grande said, “we left in spring”.", encoding="utf-8")
    assert read_source(p) == "Grande said, “we left in spring”."


def test_read_source_cp1252_fallback_recovers_curly_apostrophe(tmp_path):
    p = tmp_path / "cp1252.txt"
    p.write_bytes(b"Let\x92s start the interview.")
    text = read_source(p)
    assert "’" in text  # 0x92 in cp1252 == RIGHT SINGLE QUOTATION MARK
    assert "Let’s start the interview." == text


def test_read_source_hopeless_binary_never_raises(tmp_path):
    p = tmp_path / "binary.bin"
    p.write_bytes(bytes(range(256)) * 2)
    text = read_source(p)  # must not raise, even though this isn't real cp1252-decodable text
    assert isinstance(text, str)
