"""The audio path (P10.1b, data-session-spec.md §12): upload -> Voxtral ASR (diarized) -> role
mapping -> canonical transcript -> optional gated redraft -> the EXISTING ingest machinery.
Nothing downstream of the rendered .txt changes: this module's whole job is to produce a normal
`NAME:\ttext` transcript (see engine/seed_data/*.txt) that ingest.ingest() already knows how to
eat.

ASR is a plain multipart POST, not the OpenAI-compatible chat client llm.py wraps — Voxtral has
no chat-completions shape — so this module talks httpx directly instead of going through
masshine.llm's client. It ALWAYS uses the Mistral credentials (MISTRAL_API_KEY, fallback
MASSHINE_MISTRAL_API_KEY) regardless of MASSHINE_PROVIDER/MASSHINE_LLM_BACKEND: Voxtral is the
only ASR provider this engine has, so there is no profile to switch. Role mapping and the
optional redraft pass DO go through llm.chat_json (they're ordinary structured JSON calls) and so
ARE covered by the offline-tests guard in tests/conftest.py.

Diarization speaker ids are only stable WITHIN one ASR call. A long recording is chunked by time
before it ever reaches Voxtral, so `segments` may span several calls — every segment carries a
`chunk` index (added here, not part of Voxtral's own response shape) and role mapping runs once
PER CHUNK; rendering stitches turns by RESOLVED ROLE/NAME, not by raw speaker_id, which is what
makes chunk boundaries invisible in the final transcript (spec §12.1).
"""
from __future__ import annotations

import difflib
import io
import os
import re
import time
import wave
from pathlib import Path

import httpx

from . import llm
from .config import PROMPTS

CHUNK_SECONDS = 12 * 60      # where a long WAV gets sliced (spec: "~10-15 min" boundaries)
LONG_AUDIO_SECONDS = 15 * 60  # duration above which a WAV is chunked even if small
MAX_SINGLE_BYTES = 50 * 1024 * 1024  # ~50MB API size comfort (spec's figure, not a hard API cap)
ROLES = ("interviewer", "interviewee", "other")
REDRAFT_MAX_CHANGE = 0.25     # per-segment word-change ceiling before the gate rejects it
REDRAFT_MAX_LEN_DELTA = 0.15  # per-segment word-COUNT delta ceiling (add/remove guard)
ROLE_HEAD_SEGMENTS = 20        # how much of a chunk's opening the role-mapping call gets to see


# ---- ASR call -------------------------------------------------------------------------------

def _api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("MASSHINE_MISTRAL_API_KEY")
    if not key:
        raise RuntimeError(
            "set MISTRAL_API_KEY (or MASSHINE_MISTRAL_API_KEY) to transcribe audio — ASR always "
            "uses the Mistral credentials regardless of MASSHINE_PROVIDER/MASSHINE_LLM_BACKEND; "
            "Voxtral is the only ASR provider this engine has.")
    return key


def _post_transcription(data: bytes, filename: str, model: str, timeout: float) -> dict:
    """One real multipart call to Voxtral. Records usage into the shared llm ledger under label
    "asr" (llm.record_audio_usage) so `llm.usage()` reports audio cost the same way it reports
    chat-call cost — this is the whole of this module's ledger hook."""
    base = os.environ.get("MASSHINE_MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
    t0 = time.perf_counter()
    resp = httpx.post(
        f"{base}/audio/transcriptions",
        headers={"Authorization": f"Bearer {_api_key()}"},
        files={"file": (filename, data)},
        data={"model": model, "diarize": "true", "timestamp_granularities[]": "segment"},
        timeout=timeout,
    )
    wall = time.perf_counter() - t0
    resp.raise_for_status()
    payload = resp.json()
    usage = payload.get("usage") or {}
    llm.record_audio_usage(
        "asr",
        prompt_audio_seconds=float(usage.get("prompt_audio_seconds", 0) or 0),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        wall_s=wall,
    )
    return payload


def _tag_chunk(resp: dict, chunk: int, base_time: float = 0.0) -> dict:
    """Stamp every segment with its chunk index (and offset start/end by the chunk's base time in
    the original recording) — the field role mapping and rendering stitch on."""
    out = dict(resp)
    segs = []
    for s in resp.get("segments", []) or []:
        s = dict(s)
        s["start"] = float(s.get("start", 0) or 0) + base_time
        s["end"] = float(s.get("end", 0) or 0) + base_time
        s["chunk"] = chunk
        segs.append(s)
    out["segments"] = segs
    return out


# ---- WAV time-slicing (stdlib `wave` only — see the compressed-format guard below) -----------

def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        return (w.getnframes() / rate) if rate else 0.0


def _split_wav_chunks(path: Path, chunk_seconds: float) -> list[tuple[bytes, float]]:
    """[(chunk_wav_bytes, base_time_seconds), ...], slicing frames sequentially at
    `chunk_seconds` boundaries. Pure frame-count math, no re-encoding — every chunk is a valid
    standalone WAV with the source's own params."""
    chunks: list[tuple[bytes, float]] = []
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        rate = w.getframerate()
        nframes = w.getnframes()
        frames_per_chunk = max(1, int(chunk_seconds * rate))
        base_frame = 0
        while base_frame < nframes:
            frames = w.readframes(frames_per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setparams(params)
                out.writeframes(frames)
            chunks.append((buf.getvalue(), base_frame / rate))
            base_frame += frames_per_chunk
    return chunks


def _transcribe_wav_chunked(path: Path, model: str, timeout: float) -> dict:
    chunks = _split_wav_chunks(path, CHUNK_SECONDS)
    segments: list[dict] = []
    texts: list[str] = []
    totals = {"prompt_audio_seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
              "total_tokens": 0}
    audio_tokens = 0
    language = out_model = finish_reason = None
    for idx, (chunk_bytes, base_time) in enumerate(chunks):
        resp = _post_transcription(chunk_bytes, f"{path.stem}.chunk{idx}.wav", model, timeout)
        tagged = _tag_chunk(resp, idx, base_time)
        segments.extend(tagged["segments"])
        texts.append(resp.get("text", "") or "")
        u = resp.get("usage") or {}
        for k in totals:
            totals[k] += float(u.get(k, 0) or 0) if k == "prompt_audio_seconds" else int(u.get(k, 0) or 0)
        audio_tokens += int((u.get("prompt_tokens_details") or {}).get("audio_tokens", 0) or 0)
        language, out_model, finish_reason = (resp.get("language"), resp.get("model"),
                                               resp.get("finish_reason"))
    totals["prompt_tokens_details"] = {"audio_tokens": audio_tokens}
    return {"text": " ".join(t for t in texts if t), "segments": segments, "language": language,
            "model": out_model or model, "usage": totals, "finish_reason": finish_reason,
            "n_chunks": len(chunks)}


def transcribe_audio(path: str | Path, *, model: str | None = None, timeout: float = 600) -> dict:
    """Transcribe one audio file -> the parsed Voxtral response dict (`text`, `segments`,
    `language`, `model`, `usage`, `finish_reason`), every segment additionally carrying a
    `chunk` index. Long or oversized WAVs are sliced by time (stdlib `wave`, no re-encoding) and
    stitched back with offset-corrected timestamps; a single call otherwise.

    Compressed formats (mp3/m4a/aiff/...) are sent whole up to MAX_SINGLE_BYTES and NOT chunked —
    slicing a compressed stream by time needs decoding, which stdlib doesn't do.
    # ponytail: ffmpeg-based time-slicing is the upgrade path for arbitrary formats when a
    # researcher's raw recording routinely exceeds the single-call comfort limit; today they can
    # convert to WAV or trim the file, so a new dependency isn't earning its keep yet.
    """
    path = Path(path)
    model = model or os.environ.get("MASSHINE_ASR_MODEL", "voxtral-mini-latest")
    size = path.stat().st_size

    if path.suffix.lower() == ".wav":
        duration = _wav_duration_seconds(path)
        if duration > LONG_AUDIO_SECONDS or size > MAX_SINGLE_BYTES:
            return _transcribe_wav_chunked(path, model, timeout)
        return _tag_chunk(_post_transcription(path.read_bytes(), path.name, model, timeout), 0)

    if size > MAX_SINGLE_BYTES:
        raise RuntimeError(
            f"{path.name} is {size / 1e6:.0f}MB, over the ~{MAX_SINGLE_BYTES // (1024 * 1024)}MB "
            f"single-call comfort limit, and {path.suffix or 'this format'} can't be time-sliced "
            "without decoding (stdlib `wave` only reads WAV). Provide a WAV file, or a shorter / "
            "lower-bitrate recording.")
    return _tag_chunk(_post_transcription(path.read_bytes(), path.name, model, timeout), 0)


# ---- role mapping (one llm.chat_json call PER CHUNK — diarization ids aren't stable across
# chunks, roles are; see the module docstring) --------------------------------------------------

def _speaker_lines(chunk_segments: list[dict], n: int = ROLE_HEAD_SEGMENTS) -> str:
    return "\n".join(f"[{s['speaker_id']}] {s.get('text', '').strip()}"
                     for s in chunk_segments[:n])


def map_roles(segments: list[dict]) -> dict[str, dict]:
    """{"<chunk>:<speaker_id>": {"role": "interviewer|interviewee|other", "name": str}} covering
    every (chunk, speaker_id) pair actually present in `segments`. One llm.chat_json call per
    chunk (roles.prompt): the chunk's opening lines + its full speaker inventory in, a role/name
    per speaker id out.

    Python disposes (model proposes): every speaker_id present in the chunk gets an entry
    (missing from the model's answer -> default role "other", name ""); ids the model invented
    that aren't actually in this chunk are silently dropped; a role outside the enum is coerced
    to "other"."""
    system = (PROMPTS / "roles.prompt").read_text(encoding="utf-8")
    by_chunk: dict[int, list[dict]] = {}
    for s in segments:
        by_chunk.setdefault(int(s.get("chunk", 0)), []).append(s)

    roles: dict[str, dict] = {}
    for chunk, chunk_segments in sorted(by_chunk.items()):
        speaker_ids = sorted({s["speaker_id"] for s in chunk_segments})
        user = f"{_speaker_lines(chunk_segments)}\n\nSPEAKERS: {', '.join(speaker_ids)}"
        data = llm.chat_json(system, user, label="asr-roles")
        raw = data.get("speakers") if isinstance(data, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        for sid in speaker_ids:  # only ids actually present survive — invented ids drop here
            entry = raw.get(sid) or {}
            role = str(entry.get("role", "")).strip().lower()
            if role not in ROLES:
                role = "other"
            name = str(entry.get("name") or "").strip()
            roles[f"{chunk}:{sid}"] = {"role": role, "name": name}
    return roles


# ---- render: diarized segments + roles -> the canonical `\tNAME:\ttext` transcript ------------

def _display_name(segment: dict, roles: dict[str, dict]) -> str:
    key = f"{segment.get('chunk', 0)}:{segment['speaker_id']}"
    r = roles.get(key) or {"role": "other", "name": ""}
    return (r.get("name") or "").strip().upper() or r.get("role", "other").upper()


def render_transcript(segments: list[dict], roles: dict[str, dict]) -> tuple[str, dict]:
    """(txt, sidecar). Consecutive segments that resolve to the same display name (mapped name,
    else role) merge into one turn — this is the "stitch by role" from the module docstring: two
    chunks' differently-numbered speaker_ids both mapped to "interviewer" render as one column,
    the chunk boundary invisible. Turns render as `\tNAME:\ttext` (blank line between turns),
    matching engine/seed_data/*.txt exactly, so ingest.ingest() eats the result unchanged.

    sidecar is the full segments+roles JSON (timestamps preserved) — the evidence-door-plays-
    the-narrator's-voice future is one join away on this, not built here (spec §12)."""
    turns: list[tuple[str, list[dict]]] = []
    for s in segments:
        name = _display_name(s, roles)
        if turns and turns[-1][0] == name:
            turns[-1][1].append(s)
        else:
            turns.append((name, [s]))

    lines = []
    for name, segs in turns:
        text = " ".join(seg.get("text", "").strip() for seg in segs if seg.get("text", "").strip())
        lines.append(f"\t{name}:\t{text}")
    txt = "\n\n".join(lines) + ("\n" if lines else "")
    sidecar = {"segments": segments, "roles": roles}
    return txt, sidecar


# ---- optional gated redraft pass (pre-ingest only) --------------------------------------------

def _norm_word(w: str) -> str:
    """Strip leading/trailing punctuation and lowercase — casing and end-punctuation are exactly
    what a conservative redraft is FOR, so they must not count as "changed" words; a substituted
    word (a real mis-hearing fix) still differs after normalizing."""
    return re.sub(r"^\W+|\W+$", "", w).lower()


def redraft_gate(orig: str, fixed: str, max_change: float = REDRAFT_MAX_CHANGE) -> tuple[bool, float]:
    """(accepted, changed_ratio) for one segment's proposed redraft. `changed_ratio` is a
    word-level edit-distance ratio (1 - difflib.SequenceMatcher similarity over case/punctuation-
    normalized words, stdlib only — no new dependency for a conservative-cleanup gate). Rejects
    outright (no override — see the module docstring) if the NORMALIZED words changed exceed
    `max_change`, OR the raw word COUNT itself moved by more than REDRAFT_MAX_LEN_DELTA (guards
    against a redraft that quietly adds or drops content)."""
    orig_words, fixed_words = orig.split(), fixed.split()
    if not orig_words:
        return (fixed.strip() == orig.strip()), 0.0
    a = [_norm_word(w) for w in orig_words]
    b = [_norm_word(w) for w in fixed_words]
    changed = 1.0 - difflib.SequenceMatcher(None, a, b).ratio()
    len_delta = abs(len(fixed_words) - len(orig_words)) / len(orig_words)
    return (changed <= max_change and len_delta <= REDRAFT_MAX_LEN_DELTA), changed


def propose_redraft(segments: list[dict],
                    max_change: float = REDRAFT_MAX_CHANGE) -> tuple[list[dict], dict]:
    """(accepted_segments, rejection_stats). One llm.chat_json call PER CHUNK (redraft.prompt):
    conservative cleanup only — punctuation, casing, obvious mis-hearings; the prompt forbids
    paraphrase/reorder/deletion, and the model may omit a segment entirely to mean "unchanged".

    `accepted_segments` is a copy of `segments`: a segment's text is replaced (and flagged
    `redrafted: True`) ONLY where a proposed change passed redraft_gate; a gate-rejected or
    never-proposed segment is returned byte-for-byte as given — this NEVER touches the stored
    transcript itself (see api.py's separate propose/apply endpoints)."""
    system = (PROMPTS / "redraft.prompt").read_text(encoding="utf-8")
    by_chunk: dict[int, list[tuple[int, dict]]] = {}
    for i, s in enumerate(segments):
        by_chunk.setdefault(int(s.get("chunk", 0)), []).append((i, s))

    out = [dict(s) for s in segments]
    stats = {"proposed": 0, "accepted": 0, "rejected": 0}
    for chunk, items in sorted(by_chunk.items()):
        user = "\n".join(f"[{i}] {s.get('text', '')}" for i, s in items)
        data = llm.chat_json(system, user, label="asr-redraft")
        rows = data.get("segments") if isinstance(data, dict) else None
        fixed_by_index: dict[int, str] = {}
        for row in rows or []:
            try:
                idx = int(row.get("index"))
            except (TypeError, ValueError, AttributeError):
                continue
            fixed_by_index[idx] = str(row.get("text", ""))
        for i, s in items:
            fixed = fixed_by_index.get(i)
            orig = s.get("text", "")
            if fixed is None or fixed.strip() == orig.strip():
                continue
            stats["proposed"] += 1
            accepted, _ratio = redraft_gate(orig, fixed, max_change)
            if accepted:
                out[i]["text"] = fixed
                out[i]["redrafted"] = True
                stats["accepted"] += 1
            else:
                stats["rejected"] += 1
    return out, stats


if __name__ == "__main__":
    # ponytail: lazy self-check, not a pytest file — see tests/test_transcribe.py for the real
    # suite. Run with `.venv/bin/python3 -m masshine.transcribe` from engine/.
    segs = [
        {"chunk": 0, "speaker_id": "speaker_1", "start": 0.0, "end": 1.0, "text": "Hello there."},
        {"chunk": 0, "speaker_id": "speaker_1", "start": 1.0, "end": 2.0, "text": "I'm Andrew."},
        {"chunk": 0, "speaker_id": "speaker_2", "start": 2.0, "end": 3.0, "text": "Hi, I'm Mary."},
        {"chunk": 1, "speaker_id": "speaker_9", "start": 0.0, "end": 1.0, "text": "One more thing."},
    ]
    roles = {"0:speaker_1": {"role": "interviewer", "name": "Andrew"},
             "0:speaker_2": {"role": "interviewee", "name": "Mary"},
             "1:speaker_9": {"role": "interviewer", "name": ""}}
    txt, sidecar = render_transcript(segs, roles)
    # chunk 1's speaker has no NAME mapped -> falls back to its ROLE ("interviewer"), which is
    # still the same column as chunk 0's Andrew: stitching is by role, not by name (spec §12.1).
    assert txt == ("\tANDREW:\tHello there. I'm Andrew.\n\n"
                    "\tMARY:\tHi, I'm Mary.\n\n"
                    "\tINTERVIEWER:\tOne more thing.\n"), txt
    ok, ratio = redraft_gate("the quick brown fox jumps", "The quick brown fox jumps.")
    assert ok and ratio < 0.25
    ok, ratio = redraft_gate("the quick brown fox jumps", "A slow gray cat sleeps quietly now")
    assert not ok
    print("transcribe.py self-check OK")
