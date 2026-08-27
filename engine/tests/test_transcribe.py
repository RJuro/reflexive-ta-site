"""The audio path (P10.1b, data-session-spec.md §12): ASR response parsing + ledger accounting,
WAV time-slicing + timestamp offsetting + role-based stitching, the role validator, canonical
render shape (and that ingest actually eats it), the redraft gate, and the API flow end to end.
All offline: `transcribe.httpx.post` is blocked by default (see `_no_live_http` below) the same
way conftest.py blocks `llm.chat_json` — every test that reaches the ASR call stubs it itself."""
from __future__ import annotations

import io
import json
import sqlite3
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import llm
import masshine as m
from masshine import api as api_mod
from masshine import jobs, projects, transcribe
from masshine.api import app
from masshine.db import init_db


@pytest.fixture(autouse=True)
def _no_live_http(monkeypatch):
    def _blocked(*a, **k):
        raise AssertionError(
            "transcribe.httpx.post was called without a stub — the suite is offline; "
            "monkeypatch transcribe.httpx.post with a canned response.")
    monkeypatch.setattr(transcribe.httpx, "post", _blocked)


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"fake HTTP {self.status_code}")

    def json(self):
        return self._payload


def _make_wav(path: Path, seconds: float, framerate: int = 100) -> None:
    n_frames = int(seconds * framerate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00\x00" * n_frames)


# ---- ASR response parsing + ledger accounting --------------------------------------------------

def test_transcribe_audio_single_call_parses_response_and_records_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    llm.reset_usage()
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"fake-mp3-bytes")

    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "data": data})
        return _FakeResp({
            "text": "hello world",
            "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                          "text": "hello world"}],
            "language": "en", "model": "voxtral-mini-latest",
            "usage": {"prompt_audio_seconds": 12.5, "prompt_tokens": 100, "completion_tokens": 20,
                     "total_tokens": 120, "prompt_tokens_details": {"audio_tokens": 90}},
            "finish_reason": "stop",
        })
    monkeypatch.setattr(transcribe.httpx, "post", fake_post)

    resp = transcribe.transcribe_audio(audio)
    assert resp["segments"] == [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                                 "text": "hello world", "chunk": 0}]
    assert resp["text"] == "hello world"

    assert calls[0]["url"].endswith("/audio/transcriptions")
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0]["data"]["model"] == "voxtral-mini-latest"
    assert calls[0]["data"]["diarize"] == "true"
    assert calls[0]["data"]["timestamp_granularities[]"] == "segment"

    usage = llm.usage()
    assert usage["audio_seconds"] == 12.5
    by_label = usage["by_label"]["asr"]
    assert by_label == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20,
                        "cached_tokens": 0, "think_chars": 0, "json_chars": 0,
                        "wall_s": pytest.approx(by_label["wall_s"]), "audio_seconds": 12.5}


def test_transcribe_audio_requires_mistral_key_and_never_touches_the_network(tmp_path, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("MASSHINE_MISTRAL_API_KEY", raising=False)
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        transcribe.transcribe_audio(audio)  # _no_live_http would fail the test if this posted


def test_transcribe_audio_rejects_oversized_compressed_file_without_chunking(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setattr(transcribe, "MAX_SINGLE_BYTES", 10)  # force the oversize path cheaply
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"x" * 100)
    with pytest.raises(RuntimeError, match="WAV"):
        transcribe.transcribe_audio(audio)


# ---- WAV time-slicing: frame boundaries + timestamp offsetting ---------------------------------

def test_split_wav_chunks_slices_at_frame_boundaries_and_offsets_base_time(tmp_path):
    wav = tmp_path / "long.wav"
    _make_wav(wav, seconds=5, framerate=100)  # 500 frames total
    chunks = transcribe._split_wav_chunks(wav, chunk_seconds=2)  # 200 frames/chunk
    assert [base for _, base in chunks] == [0.0, 2.0, 4.0]
    with wave.open(io.BytesIO(chunks[0][0])) as w:
        assert w.getnframes() == 200
    with wave.open(io.BytesIO(chunks[1][0])) as w:
        assert w.getnframes() == 200
    with wave.open(io.BytesIO(chunks[2][0])) as w:
        assert w.getnframes() == 100  # remainder chunk, not padded


def test_wav_duration_seconds(tmp_path):
    wav = tmp_path / "short.wav"
    _make_wav(wav, seconds=3.5, framerate=200)
    assert transcribe._wav_duration_seconds(wav) == pytest.approx(3.5)


def test_transcribe_audio_chunks_long_wav_offsets_timestamps_and_sums_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setattr(transcribe, "LONG_AUDIO_SECONDS", 1)   # force chunking on a short test wav
    monkeypatch.setattr(transcribe, "CHUNK_SECONDS", 2)
    wav = tmp_path / "long.wav"
    _make_wav(wav, seconds=5, framerate=100)  # -> 3 chunks (2s, 2s, 1s), bases 0/2/4

    responses = [
        {"text": "a", "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                                    "text": "a"}],
         "usage": {"prompt_audio_seconds": 2.0, "prompt_tokens": 10, "completion_tokens": 2}},
        {"text": "b", "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                                    "text": "b"}],
         "usage": {"prompt_audio_seconds": 2.0, "prompt_tokens": 10, "completion_tokens": 2}},
        {"text": "c", "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_2",
                                    "text": "c"}],
         "usage": {"prompt_audio_seconds": 1.0, "prompt_tokens": 5, "completion_tokens": 1}},
    ]
    seen = {"n": 0}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        r = _FakeResp(responses[seen["n"]])
        seen["n"] += 1
        return r
    monkeypatch.setattr(transcribe.httpx, "post", fake_post)

    resp = transcribe.transcribe_audio(wav)
    assert resp["n_chunks"] == 3
    assert [s["chunk"] for s in resp["segments"]] == [0, 1, 2]
    assert [s["start"] for s in resp["segments"]] == [0.0, 2.0, 4.0]  # offset by each chunk's base
    assert [s["end"] for s in resp["segments"]] == [1.0, 3.0, 5.0]
    assert resp["text"] == "a b c"
    assert resp["usage"]["prompt_audio_seconds"] == 5.0
    assert resp["usage"]["prompt_tokens"] == 25


# ---- role validator: full coverage, invented ids dropped, bad role coerced ---------------------

def test_map_roles_covers_present_drops_invented_coerces_bad_role(monkeypatch):
    segments = [
        {"chunk": 0, "speaker_id": "speaker_1", "text": "Hello, this is Andrew."},
        {"chunk": 0, "speaker_id": "speaker_2", "text": "Hi, Mary here."},
        {"chunk": 1, "speaker_id": "speaker_5", "text": "Let's continue."},
    ]
    per_chunk = {
        0: {"speakers": {"speaker_1": {"role": "interviewer", "name": "Andrew"},
                         "speaker_2": {"role": "weird_role", "name": "Mary"},   # bad role
                         "speaker_99": {"role": "interviewee", "name": "Ghost"}}},  # invented id
        1: {"speakers": {}},  # missing speaker_5 entirely
    }
    labels = []

    def fake_chat_json(system, user, timeout=None, retries=None, label=""):
        labels.append(label)
        chunk = 0 if "speaker_1" in user else 1
        return per_chunk[chunk]
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    roles = transcribe.map_roles(segments)
    assert roles == {
        "0:speaker_1": {"role": "interviewer", "name": "Andrew"},
        "0:speaker_2": {"role": "other", "name": "Mary"},        # coerced (not in the enum)
        "1:speaker_5": {"role": "other", "name": ""},            # missing -> defaulted
    }
    assert "0:speaker_99" not in str(roles)  # invented id never made it in
    assert labels == ["asr-roles", "asr-roles"]  # one call per chunk


# ---- render format: seed_data shape, and ingest actually eats it -------------------------------

def test_render_transcript_matches_seed_shape_and_stitches_by_role(tmp_path, monkeypatch):
    segments = [
        {"chunk": 0, "speaker_id": "speaker_1", "start": 0.0, "end": 2.0,
         "text": "This is Andrew Phillips."},
        {"chunk": 0, "speaker_id": "speaker_1", "start": 2.0, "end": 4.0,
         "text": "I'm here with Mary Grande."},
        {"chunk": 0, "speaker_id": "speaker_2", "start": 4.0, "end": 6.0,
         "text": "My maiden name was Yankovik."},
        {"chunk": 1, "speaker_id": "speaker_9", "start": 0.0, "end": 1.0,
         "text": "One more question."},  # different chunk, same role, no name -> same column
    ]
    roles = {"0:speaker_1": {"role": "interviewer", "name": "Phillips"},
             "0:speaker_2": {"role": "interviewee", "name": "Grande"},
             "1:speaker_9": {"role": "interviewer", "name": ""}}
    txt, sidecar = transcribe.render_transcript(segments, roles)

    turns = txt.rstrip("\n").split("\n\n")
    assert turns == [
        "\tPHILLIPS:\tThis is Andrew Phillips. I'm here with Mary Grande.",
        "\tGRANDE:\tMy maiden name was Yankovik.",
        "\tINTERVIEWER:\tOne more question.",
    ]
    assert sidecar == {"segments": segments, "roles": roles}

    # ingest's own machinery must accept this shape unchanged (P1: zero ingest changes)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "sections": [{"gist": "whole", "start_line": 1, "end_line": 99}]})
    p = tmp_path / "sample.txt"
    p.write_text(txt, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    doc_id, secs, sents = m.ingest(conn, "R1", p)
    assert secs and sents
    assert conn.execute("SELECT text FROM document WHERE id=?", (doc_id,)).fetchone()[0] == txt
    conn.close()


# ---- redraft gate: over-changed rejected, word-count guard, accepted diff applied ---------------

def test_redraft_gate_accepts_conservative_casing_and_punctuation_fix():
    accepted, changed = transcribe.redraft_gate(
        "the quick brown fox jumps", "The quick brown fox jumps.")
    assert accepted and changed < 0.25


def test_redraft_gate_rejects_a_paraphrase():
    accepted, _ = transcribe.redraft_gate(
        "the interviewer asked about the journey across the ocean",
        "she felt a deep and complicated grief about leaving home forever")
    assert not accepted


def test_redraft_gate_word_count_guard_rejects_even_with_lenient_max_change():
    orig = "we left in the spring of nineteen twenty"
    fixed = "we left in the early spring of nineteen twenty as planned months before"
    accepted, _ = transcribe.redraft_gate(orig, fixed, max_change=0.9)
    assert not accepted  # length moved by way more than 15% regardless of word overlap


def test_propose_redraft_applies_gate_per_segment(monkeypatch):
    segments = [
        {"chunk": 0, "text": "the interviewer asked about teh journey"},   # obvious typo -> fix
        {"chunk": 0, "text": "a long rambling answer about many things"},  # bogus rewrite -> reject
        {"chunk": 0, "text": "unchanged segment text here"},               # model leaves alone
    ]

    def fake_chat_json(system, user, timeout=None, retries=None, label=""):
        assert label == "asr-redraft"
        return {"segments": [
            {"index": 0, "text": "The interviewer asked about the journey."},
            {"index": 1, "text": "completely different words replacing everything said before"},
        ]}
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    accepted, stats = transcribe.propose_redraft(segments)
    assert stats == {"proposed": 2, "accepted": 1, "rejected": 1}
    assert accepted[0]["text"] == "The interviewer asked about the journey."
    assert accepted[0]["redrafted"] is True
    assert accepted[1]["text"] == segments[1]["text"]      # rejected -> original kept
    assert "redrafted" not in accepted[1]
    assert accepted[2]["text"] == segments[2]["text"]      # never proposed -> untouched
    assert "redrafted" not in accepted[2]


# ---- API flow: upload -> job -> txt+sidecar -> ingested; 409s; redraft/apply ------------------

@pytest.fixture
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return projects.create_project("Audio Test")["id"]


def test_audio_upload_auto_ingests_and_then_refuses_redraft(pid, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setattr(transcribe.httpx, "post", lambda *a, **k: _FakeResp({
        "text": "hi",
        "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                      "text": "This is Andrew."},
                     {"start": 1.0, "end": 2.0, "speaker_id": "speaker_2",
                      "text": "Hi, I'm Mary."}],
        "usage": {"prompt_audio_seconds": 3.0, "prompt_tokens": 50, "completion_tokens": 10},
    }))

    def fake_chat_json(system, user, timeout=None, retries=None, label=""):
        if label == "asr-roles":
            return {"speakers": {"speaker_1": {"role": "interviewer", "name": "Andrew"},
                                 "speaker_2": {"role": "interviewee", "name": "Mary"}}}
        if label == "structure":
            return {"sections": [{"gist": "whole", "start_line": 1, "end_line": 20}]}
        raise AssertionError(f"unexpected label {label!r}")
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append((jid, work)))
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/audio",
                    files={"file": ("interview.mp3", b"fake audio bytes", "audio/mpeg")})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    jid, work = submitted[-1]
    assert jid == job_id
    jobs._run(job_id, work)

    done = projects.get_job(job_id)
    assert done["status"] == "done", done.get("error")
    result = done["result"]
    assert result["n_segments"] == 2
    assert result["n_speakers"] == 2
    assert result["redraft_available"] is False   # auto_ingest defaulted True -> already ingested
    assert result["roles"]["0:speaker_1"]["name"] == "Andrew"

    uploads = projects.uploads_dir(pid)
    assert (uploads / "interview.txt").exists()
    assert (uploads / "interview.asr.json").exists()
    assert "ANDREW" in (uploads / "interview.txt").read_text()

    doc_id = result["ingest"]["doc_id"]
    from masshine.db import project_db
    conn = project_db(projects.project_db_path(pid))
    row = conn.execute("SELECT status FROM document WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    assert row == ("ingested",)

    assert client.post(f"/projects/{pid}/audio/interview/redraft").status_code == 409
    assert client.post(f"/projects/{pid}/audio/interview/redraft/apply",
                       json={"indices": "all"}).status_code == 409


def test_two_step_flow_review_redraft_apply_then_explicit_ingest(pid, tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setattr(transcribe.httpx, "post", lambda *a, **k: _FakeResp({
        "text": "hi",
        "segments": [{"start": 0.0, "end": 1.0, "speaker_id": "speaker_1",
                      "text": "the quick brown fox"},
                     {"start": 1.0, "end": 2.0, "speaker_id": "speaker_1",
                      "text": "jumps over teh lazy dog"}],
        "usage": {"prompt_audio_seconds": 2.0, "prompt_tokens": 10, "completion_tokens": 2},
    }))

    def fake_chat_json(system, user, timeout=None, retries=None, label=""):
        if label == "asr-roles":
            return {"speakers": {"speaker_1": {"role": "interviewer", "name": ""}}}
        if label == "asr-redraft":
            return {"segments": [{"index": 1, "text": "jumps over the lazy dog"}]}
        if label == "structure":
            return {"sections": [{"gist": "whole", "start_line": 1, "end_line": 5}]}
        raise AssertionError(f"unexpected label {label!r}")
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append((jid, work)))
    client = TestClient(app)

    wav = tmp_path / "clip.wav"
    _make_wav(wav, seconds=1)  # short WAV: real RIFF bytes, single-call path (no chunking)
    r = client.post(f"/projects/{pid}/audio?auto_ingest=false",
                    files={"file": ("clip.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 200
    jid, work = submitted[-1]
    jobs._run(jid, work)
    done = projects.get_job(jid)
    assert done["status"] == "done", done.get("error")
    assert done["result"]["redraft_available"] is True
    assert "ingest" not in done["result"]

    tr = client.get(f"/projects/{pid}/audio/clip/transcript")
    assert tr.status_code == 200
    orig_text = tr.json()["text"]
    assert "teh lazy dog" in orig_text

    rd = client.post(f"/projects/{pid}/audio/clip/redraft")
    assert rd.status_code == 200
    body = rd.json()
    assert body["stats"] == {"proposed": 1, "accepted": 1, "rejected": 0}
    assert body["diff"] == [{"index": 1, "orig": "jumps over teh lazy dog",
                             "fixed": "jumps over the lazy dog"}]

    ap = client.post(f"/projects/{pid}/audio/clip/redraft/apply", json={"indices": "all"})
    assert ap.status_code == 200
    assert ap.json()["applied"] == 1

    uploads = projects.uploads_dir(pid)
    new_text = (uploads / "clip.txt").read_text()
    assert "the lazy dog" in new_text and "teh lazy dog" not in new_text
    assert (uploads / "clip.orig.txt").read_text() == orig_text  # original preserved, never touched again

    r3 = client.post(f"/projects/{pid}/audio/clip/ingest")
    assert r3.status_code == 200
    jid2, work2 = submitted[-1]
    assert jid2 == r3.json()["job_id"]
    jobs._run(jid2, work2)
    assert projects.get_job(jid2)["status"] == "done"

    assert client.post(f"/projects/{pid}/audio/clip/redraft").status_code == 409
    assert client.post(f"/projects/{pid}/audio/clip/redraft/apply",
                       json={"indices": "all"}).status_code == 409


def test_audio_upload_rejects_bad_extension_and_oversized_file(pid, monkeypatch):
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/audio", files={"file": ("notes.pdf", b"x", "application/pdf")})
    assert r.status_code == 400

    monkeypatch.setattr(api_mod, "AUDIO_MAX_BYTES", 4)
    r2 = client.post(f"/projects/{pid}/audio", files={"file": ("clip.mp3", b"12345", "audio/mpeg")})
    assert r2.status_code == 413
