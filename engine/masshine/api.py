"""FastAPI surface (Phase 3). Drives the whole workbench loop the UI needs:
create project → upload .txt → run coding (standard | standpoint panel) as background jobs →
read codes / friction → run sequential theming (the catalogue) → poll jobs. Machinery (runs, prompt
versions) stays backstage; endpoints return researcher-facing artifacts.

Serve:  .venv/bin/uvicorn app:app   (engine/app.py re-exports this app)
"""
from __future__ import annotations

import os
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs, packs, projects, store
from .auth import PinAuthMiddleware
from .config import ROOT
from .db import project_db
from .ingest import _slug


def _maybe_seed_demo() -> None:
    """First boot on an empty data volume → seed the bundled demo project (0 LLM calls; see
    seed.import_cache). Skipped once any project exists, so this never re-runs or overwrites
    real work. Opt out with MASSHINE_SEED_DEMO=0 (e.g. a fresh deployment for real research)."""
    if os.environ.get("MASSHINE_SEED_DEMO", "1") in ("0", "false", "False"):
        return
    try:
        if projects.list_projects():
            return
        seed_dir = ROOT / "seed_data"
        cache = seed_dir / "panel_2interview.json"
        if not cache.exists():
            return
        from .seed import import_cache
        pid = import_cache(cache, "Migration panel (demo)", "migration_oral_history",
                           source_dir=seed_dir)
        print(f"[startup] seeded demo project {pid}", flush=True)
    except Exception as e:  # a seeding hiccup must never block the app from starting
        print(f"[startup] demo seed skipped: {type(e).__name__}: {e}", flush=True)


@asynccontextmanager
async def _lifespan(_app):
    n = projects.reset_stale_jobs()  # jobs left mid-flight by a crash → interrupted (resumable)
    if n:
        print(f"[startup] reset {n} stale job(s) to interrupted", flush=True)
    _maybe_seed_demo()
    yield


app = FastAPI(title="MASSHINE", lifespan=_lifespan)
app.add_middleware(PinAuthMiddleware)  # no-op unless MASSHINE_PIN is set (shared deployments)


@app.get("/health")
def health():
    return {"ok": True}


# ---- request models -----------------------------------------------------------------------------

class NewProject(BaseModel):
    name: str
    pack_id: str | None = None


class CodeReq(BaseModel):
    mode: str = "standard"          # "standard" | "panel"
    recode: bool = False


class ThemeReq(BaseModel):
    mode: str = "standard"
    feedback: bool = False          # True → open theme comments/revisions ride into the walk


class RecodeReq(BaseModel):
    doc_id: str
    mode: str = "standard"


class CommentReq(BaseModel):
    target_type: str                # 'sentence' | 'code' | 'theme' | 'document'
    target_id: str
    doc_id: str | None = None
    body: str
    context: dict | None = None     # snapshot (label/quote/lens) — keeps meaning across recodes


class CommentPatch(BaseModel):
    body: str | None = None         # edit the text (re-opens the comment)
    status: str | None = None       # 'open' | 'addressed' | 'dismissed'


class ReviseReq(BaseModel):
    action: str                     # 'rename' | 'reject' | 'restore'
    new_label: str | None = None


class MemoReq(BaseModel):
    target_type: str                # 'code' | 'theme' | 'document' | 'project'
    target_id: str
    body: str                       # empty body deletes the memo
    context: dict | None = None


def _require_project(pid: str) -> dict:
    p = projects.get_project(pid)
    if not p:
        raise HTTPException(404, f"no project {pid}")
    return p


def _conn(pid: str):
    return project_db(projects.project_db_path(pid))


# ---- projects -----------------------------------------------------------------------------------

@app.get("/packs")
def get_packs():
    return packs.list_packs()


@app.post("/projects")
def create_project(req: NewProject):
    if req.pack_id and req.pack_id not in {p["id"] for p in packs.list_packs()}:
        raise HTTPException(400, f"unknown pack {req.pack_id}")
    return projects.create_project(req.name, req.pack_id)


@app.get("/projects")
def list_projects():
    return projects.list_projects()


@app.get("/projects/{pid}")
def get_project(pid: str):
    proj = _require_project(pid)
    mode = "panel" if proj.get("pack_id") else "standard"
    conn = _conn(pid)
    try:
        docs = store.document_list(conn)
        counts = store.code_counts(conn)
        open_comments = store.open_comment_counts(conn)
        n_themes = conn.execute(
            "SELECT COUNT(*) FROM theme_v2 WHERE mode=?", (mode,)).fetchone()[0]
        stale = store.themes_stale(conn, mode)
    finally:
        conn.close()
    return {"project": proj, "documents": docs, "code_counts": counts, "mode": mode,
            "open_comments": open_comments, "n_themes": n_themes, "themes_stale": stale,
            "active_jobs": projects.active_jobs(pid)}


# ---- documents ----------------------------------------------------------------------------------

SOURCE_KINDS = {"transcript", "fieldnotes", "focusgroup", "document", "other"}


@app.post("/projects/{pid}/documents")
async def upload_document(pid: str, file: UploadFile, kind: str = Form("transcript")):
    _require_project(pid)
    if not (file.filename or "").lower().endswith((".txt", ".md")):
        raise HTTPException(400, "upload a plain-text source (.txt or .md)")
    if kind not in SOURCE_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(SOURCE_KINDS)}")
    dest = projects.uploads_dir(pid) / file.filename
    slug = _slug(Path(file.filename))
    n = 2  # avoid slug collisions across different uploads (P7 index needs unique doc ids)
    conn = _conn(pid)
    try:
        existing = {r[0] for r in conn.execute("SELECT id FROM document")}
    finally:
        conn.close()
    stem = slug
    while stem in existing:
        stem = f"{slug}-{n}"
        dest = projects.uploads_dir(pid) / f"{Path(file.filename).stem}-{n}.txt"
        n += 1
    dest.write_bytes(await file.read())
    job = projects.create_job(pid, "ingest", {"filename": dest.name, "kind": kind})
    jobs.submit(job["id"], jobs.ingest_work(pid, dest, kind))
    return {"job_id": job["id"], "filename": dest.name}


@app.get("/projects/{pid}/documents/{doc_id}")
def get_document(pid: str, doc_id: str):
    _require_project(pid)
    conn = _conn(pid)
    try:
        payload = store.reading_payload(conn, doc_id)
    finally:
        conn.close()
    if not payload:
        raise HTTPException(404, f"no document {doc_id}")
    return payload


# ---- coding -------------------------------------------------------------------------------------

@app.post("/projects/{pid}/code")
def run_coding(pid: str, req: CodeReq):
    _require_project(pid)
    if req.mode not in ("standard", "panel"):
        raise HTTPException(400, "mode must be 'standard' or 'panel'")
    job = projects.create_job(pid, f"code_{req.mode}", {"mode": req.mode, "recode": req.recode})
    jobs.submit(job["id"], jobs.code_work(pid, req.mode, req.recode))
    return {"job_id": job["id"]}


@app.get("/projects/{pid}/codes")
def get_codes(pid: str, coder: str | None = None, doc_id: str | None = None):
    _require_project(pid)
    conn = _conn(pid)
    try:
        return store.codes_payload(conn, coder=coder, doc_id=doc_id)
    finally:
        conn.close()


@app.get("/projects/{pid}/friction/{doc_id}")
def get_friction(pid: str, doc_id: str):
    _require_project(pid)
    conn = _conn(pid)
    try:
        return store.friction_payload(conn, doc_id)
    finally:
        conn.close()


# ---- recode with feedback -------------------------------------------------------------------

@app.post("/projects/{pid}/recode")
def run_recode(pid: str, req: RecodeReq):
    """Re-code ONE document with the researcher's open feedback compiled into the prompts."""
    _require_project(pid)
    if req.mode not in ("standard", "panel"):
        raise HTTPException(400, "mode must be 'standard' or 'panel'")
    conn = _conn(pid)
    try:
        if not conn.execute("SELECT 1 FROM document WHERE id=?", (req.doc_id,)).fetchone():
            raise HTTPException(404, f"no document {req.doc_id}")
    finally:
        conn.close()
    job = projects.create_job(pid, "recode", {"doc_id": req.doc_id, "mode": req.mode})
    jobs.submit(job["id"], jobs.recode_work(pid, req.doc_id, req.mode))
    return {"job_id": job["id"]}


# ---- themes -------------------------------------------------------------------------------------

@app.post("/projects/{pid}/themes")
def run_themes(pid: str, req: ThemeReq):
    _require_project(pid)
    if req.mode not in ("standard", "panel"):
        raise HTTPException(400, "mode must be 'standard' or 'panel'")
    job = projects.create_job(pid, "theme", {"mode": req.mode, "feedback": req.feedback})
    jobs.submit(job["id"], jobs.theme_work(pid, req.mode, feedback=req.feedback))
    return {"job_id": job["id"]}


@app.get("/projects/{pid}/themes")
def get_themes(pid: str, mode: str = "standard"):
    _require_project(pid)
    conn = _conn(pid)
    try:
        return store.themes_payload(conn, mode)
    finally:
        conn.close()


# ---- researcher feedback: comments (for the model) + memos (for the researcher) -----------------

@app.post("/projects/{pid}/comments")
def post_comment(pid: str, req: CommentReq):
    _require_project(pid)
    if req.target_type not in ("sentence", "code", "theme", "document"):
        raise HTTPException(400, "bad target_type")
    if not req.body.strip():
        raise HTTPException(400, "empty comment")
    conn = _conn(pid)
    try:
        return store.add_comment(conn, req.target_type, req.target_id, req.doc_id,
                                 req.body.strip(), req.context)
    finally:
        conn.close()


@app.get("/projects/{pid}/comments")
def get_comments(pid: str, doc_id: str | None = None, target_type: str | None = None,
                 status: str | None = None):
    _require_project(pid)
    conn = _conn(pid)
    try:
        return store.list_comments(conn, doc_id=doc_id, target_type=target_type, status=status)
    finally:
        conn.close()


@app.patch("/projects/{pid}/comments/{cid}")
def patch_comment(pid: str, cid: str, req: CommentPatch):
    _require_project(pid)
    if req.status and req.status not in ("open", "addressed", "dismissed"):
        raise HTTPException(400, "bad status")
    conn = _conn(pid)
    try:
        if not store.update_comment(conn, cid, body=req.body, status=req.status):
            raise HTTPException(404, f"no comment {cid}")
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/projects/{pid}/comments/{cid}")
def del_comment(pid: str, cid: str):
    _require_project(pid)
    conn = _conn(pid)
    try:
        if not store.delete_comment(conn, cid):
            raise HTTPException(404, f"no comment {cid}")
    finally:
        conn.close()
    return {"ok": True}


@app.put("/projects/{pid}/memos")
def put_memo(pid: str, req: MemoReq):
    _require_project(pid)
    if req.target_type not in ("code", "theme", "document", "project"):
        raise HTTPException(400, "bad target_type")
    conn = _conn(pid)
    try:
        return store.set_memo(conn, req.target_type, req.target_id, req.body, req.context)
    finally:
        conn.close()


@app.get("/projects/{pid}/memos")
def get_memos(pid: str, target_type: str | None = None):
    _require_project(pid)
    conn = _conn(pid)
    try:
        return store.list_memos(conn, target_type=target_type)
    finally:
        conn.close()


@app.post("/projects/{pid}/codes/{code_id}/revise")
def revise_code(pid: str, code_id: str, req: ReviseReq):
    """Researcher correction: rename / reject / restore a code. Applied at read time and
    compiled into the guidance the model sees on the next re-run."""
    _require_project(pid)
    if req.action not in ("rename", "reject", "restore"):
        raise HTTPException(400, "action must be rename | reject | restore")
    if req.action == "rename" and not (req.new_label or "").strip():
        raise HTTPException(400, "rename needs new_label")
    conn = _conn(pid)
    try:
        if not conn.execute("SELECT 1 FROM code WHERE id=?", (code_id,)).fetchone():
            raise HTTPException(404, f"no code {code_id}")
        return store.add_revision(conn, code_id, req.action,
                                  (req.new_label or "").strip() or None)
    finally:
        conn.close()


# ---- jobs + usage -------------------------------------------------------------------------------

@app.get("/jobs/{jid}")
def get_job(jid: str):
    j = projects.get_job(jid)
    if not j:
        raise HTTPException(404, f"no job {jid}")
    return j


@app.get("/projects/{pid}/jobs")
def list_jobs(pid: str):
    _require_project(pid)
    return projects.list_jobs(pid)


# ---- static frontend (mounted last so it doesn't shadow the API) --------------------------------

_WEB = ROOT.parent / "web"
if _WEB.exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
