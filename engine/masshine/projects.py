"""Project registry + job store (Phase 3). One cross-project SQLite registry tracks projects and
jobs; each project owns a directory under DATA_DIR with its own schema-v2 DB, uploaded transcripts,
resumable checkpoints, and exports.

    engine/data/registry.db
    engine/data/projects/<pid>/masshine.db
    engine/data/projects/<pid>/uploads/*.txt
    engine/data/projects/<pid>/checkpoint_{standard,panel}.json
    engine/data/projects/<pid>/exports/
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _registry() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATA_DIR / "registry.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project (
            id TEXT PRIMARY KEY, name TEXT, pack_id TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS job (
            id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, status TEXT,
            progress TEXT, params TEXT, result TEXT, error TEXT,
            created_at TEXT, started_at TEXT, finished_at TEXT
        );
        """
    )
    conn.commit()
    return conn


# ---- paths ---------------------------------------------------------------------------------------

def project_dir(pid: str) -> Path:
    return DATA_DIR / "projects" / pid


def project_db_path(pid: str) -> Path:
    return project_dir(pid) / "masshine.db"


def uploads_dir(pid: str) -> Path:
    d = project_dir(pid) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_path(pid: str, mode: str) -> Path:
    return project_dir(pid) / f"checkpoint_{mode}.json"


def exports_dir(pid: str) -> Path:
    d = project_dir(pid) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- projects ------------------------------------------------------------------------------------

def create_project(name: str, pack_id: str | None = None) -> dict:
    pid = _uid("P")
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    conn = _registry()
    conn.execute("INSERT INTO project (id, name, pack_id, created_at) VALUES (?,?,?,?)",
                 (pid, name, pack_id, _now()))
    conn.commit()
    conn.close()
    return {"id": pid, "name": name, "pack_id": pack_id, "created_at": _now()}


def _project_row(r) -> dict:
    return {"id": r[0], "name": r[1], "pack_id": r[2], "created_at": r[3]}


def list_projects() -> list[dict]:
    conn = _registry()
    rows = conn.execute("SELECT id, name, pack_id, created_at FROM project "
                        "ORDER BY created_at DESC").fetchall()
    conn.close()
    return [_project_row(r) for r in rows]


def get_project(pid: str) -> dict | None:
    conn = _registry()
    r = conn.execute("SELECT id, name, pack_id, created_at FROM project WHERE id = ?",
                     (pid,)).fetchone()
    conn.close()
    return _project_row(r) if r else None


# ---- jobs ----------------------------------------------------------------------------------------

_JOB_JSON = {"progress", "params", "result"}


def _job_row(r) -> dict:
    d = {"id": r[0], "project_id": r[1], "kind": r[2], "status": r[3],
         "progress": r[4], "params": r[5], "result": r[6], "error": r[7],
         "created_at": r[8], "started_at": r[9], "finished_at": r[10]}
    for k in _JOB_JSON:
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def create_job(project_id: str, kind: str, params: dict | None = None) -> dict:
    jid = _uid("J")
    conn = _registry()
    conn.execute(
        "INSERT INTO job (id, project_id, kind, status, params, created_at) VALUES (?,?,?,?,?,?)",
        (jid, project_id, kind, "queued", json.dumps(params or {}), _now()))
    conn.commit()
    conn.close()
    return get_job(jid)


def get_job(jid: str) -> dict | None:
    conn = _registry()
    r = conn.execute(
        "SELECT id, project_id, kind, status, progress, params, result, error, "
        "created_at, started_at, finished_at FROM job WHERE id = ?", (jid,)).fetchone()
    conn.close()
    return _job_row(r) if r else None


def list_jobs(project_id: str) -> list[dict]:
    conn = _registry()
    rows = conn.execute(
        "SELECT id, project_id, kind, status, progress, params, result, error, "
        "created_at, started_at, finished_at FROM job WHERE project_id = ? "
        "ORDER BY created_at DESC", (project_id,)).fetchall()
    conn.close()
    return [_job_row(r) for r in rows]


def update_job(jid: str, **fields) -> None:
    if not fields:
        return
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v) if k in _JOB_JSON and v is not None else v)
    vals.append(jid)
    conn = _registry()
    conn.execute(f"UPDATE job SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def active_jobs(project_id: str) -> list[dict]:
    return [j for j in list_jobs(project_id) if j["status"] in ("queued", "running")]


def reset_stale_jobs() -> int:
    """On startup, any job left running/queued (process died) is marked interrupted — the work is
    resumable by re-POSTing (checkpoint pattern). Returns how many were reset."""
    conn = _registry()
    cur = conn.execute(
        "UPDATE job SET status = 'interrupted', finished_at = ? "
        "WHERE status IN ('running', 'queued')", (_now(),))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n
