"""Delete a model file from a PC when the user asks. The agent re-hashes it before removing anything."""
import json
import time
import uuid
from fastapi import HTTPException
from .downloads import _request_scan

FIELDS = ("id", "agent", "device", "paths", "sha256", "state", "error", "created_at", "updated_at")


def ensure_tables(db):
    # state: queued -> running -> done | failed
    db.execute("""CREATE TABLE IF NOT EXISTS delete_jobs (
        id TEXT PRIMARY KEY, agent TEXT NOT NULL, device TEXT NOT NULL, paths TEXT NOT NULL, sha256 TEXT NOT NULL,
        state TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")


def _job(row):
    job = dict(zip(FIELDS, row))
    job["paths"] = json.loads(job["paths"])
    return job


def get(db, job_id):
    row = db.execute(f"SELECT {','.join(FIELDS)} FROM delete_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown delete request")
    return _job(row)


def request(db, device, path):
    if not device.endswith(":comfyui"):
        raise HTTPException(422, "Only ComfyUI model files can be deleted here")
    row = db.execute("SELECT sha256,file_id FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    if not row[0]:
        raise HTTPException(409, "This file has not been hashed yet; wait for the next Windows sync")
    sha, file_id = row
    # Hard links (the same file in a family folder) go together, or the data would stay on disk.
    paths = [r[0] for r in db.execute("SELECT path FROM models WHERE device=? AND file_id=? ORDER BY path",
                                      (device, file_id))] if file_id else [path]
    active = db.execute(f"""SELECT {','.join(FIELDS)} FROM delete_jobs WHERE device=? AND sha256=?
        AND state IN ('queued','running')""", (device, sha)).fetchone()
    if active:
        return _job(active)
    now, job_id = int(time.time()), uuid.uuid4().hex
    db.execute("INSERT INTO delete_jobs VALUES (?,?,?,?,?,'queued',NULL,?,?)",
               (job_id, device.removesuffix(":comfyui"), device, json.dumps(paths), sha, now, now))
    return get(db, job_id)


def claim(db, agent):
    row = db.execute("""SELECT id,paths,sha256 FROM delete_jobs WHERE agent=? AND state='queued'
        ORDER BY created_at LIMIT 1""", (agent,)).fetchone()
    if not row or db.execute("UPDATE delete_jobs SET state='running',updated_at=? WHERE id=? AND state='queued'",
                             (int(time.time()), row[0])).rowcount != 1:
        return None
    return {"id": row[0], "paths": json.loads(row[1]), "sha256": row[2]}


def finish(db, job_id, agent, error):
    job = get(db, job_id)
    if job["agent"] != agent or job["state"] != "running":
        raise HTTPException(409, "This delete is no longer running for this agent")
    db.execute("UPDATE delete_jobs SET state=?,error=?,updated_at=? WHERE id=?",
               ("failed" if error else "done", str(error)[:300] if error else None, int(time.time()), job_id))
    if not error:
        db.executemany("DELETE FROM models WHERE device=? AND path=?", [(job["device"], p) for p in job["paths"]])
        _request_scan(db, agent)
    return get(db, job_id)
