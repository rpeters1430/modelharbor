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
    if "runtime_meta" not in [row[1] for row in db.execute("PRAGMA table_info(delete_jobs)")]:
        db.execute("ALTER TABLE delete_jobs ADD COLUMN runtime_meta TEXT")


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
    if device.endswith(":unsloth"):
        row = db.execute("SELECT runtime_meta FROM models WHERE device=? AND path=? AND kind LIKE 'unsloth/%'", (device, path)).fetchone()
        if not row:
            raise HTTPException(404, "Model not found in inventory")
        try:
            meta = json.loads(row[0])
        except (TypeError, ValueError):
            meta = None
        if not isinstance(meta, dict) or not isinstance(meta.get("repo_id"), str) or not isinstance(meta.get("cache_path"), str) or not meta["cache_path"]:
            raise HTTPException(409, "Unsloth did not identify the cache copy; delete it in Unsloth Studio")
        active = db.execute(f"SELECT {','.join(FIELDS)} FROM delete_jobs WHERE device=? AND paths=? AND state IN ('queued','running')", (device, json.dumps([path]))).fetchone()
        if active:
            return _job(active)
        now, job_id = int(time.time()), uuid.uuid4().hex
        db.execute("INSERT INTO delete_jobs (id,agent,device,paths,sha256,state,error,created_at,updated_at,runtime_meta) VALUES (?,?,?,?,?,'queued',NULL,?,?,?)",
                   (job_id, device.rsplit(':', 1)[0], device, json.dumps([path]), '', now, now, json.dumps(meta)))
        return get(db, job_id)
    if not device.endswith((":comfyui", ":llamacpp")):
        raise HTTPException(422, "Only ComfyUI and llama.cpp model files can be deleted here")
    row = db.execute("SELECT sha256,file_id FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    if not row[0]:
        raise HTTPException(409, "This file has not been hashed yet; wait for the agent's next sync")
    sha, file_id = row
    # Hard links (the same file in a family folder) go together, or the data would stay on disk.
    paths = [r[0] for r in db.execute("SELECT path FROM models WHERE device=? AND file_id=? ORDER BY path",
                                      (device, file_id))] if file_id else [path]
    # Same paths, not just same contents: a second copy of a duplicate is its own delete.
    active = db.execute(f"""SELECT {','.join(FIELDS)} FROM delete_jobs WHERE device=? AND sha256=? AND paths=?
        AND state IN ('queued','running')""", (device, sha, json.dumps(paths))).fetchone()
    if active:
        return _job(active)
    now, job_id = int(time.time()), uuid.uuid4().hex
    db.execute("INSERT INTO delete_jobs (id,agent,device,paths,sha256,state,error,created_at,updated_at) VALUES (?,?,?,?,?,'queued',NULL,?,?)",
               (job_id, device.rsplit(":", 1)[0], device, json.dumps(paths), sha, now, now))
    return get(db, job_id)


STALE_SECONDS = 3600  # far longer than re-hashing one file takes


def claim(db, agent):
    # A delete running this long was cut off by a crash or restart. Failing it is safe to retry: the agent
    # re-hashes before deleting, so a retry never removes the wrong file.
    now = int(time.time())
    db.execute("UPDATE delete_jobs SET state='failed',error=?,updated_at=? WHERE agent=? AND state='running' AND updated_at<?",
               ("Interrupted: the PC's background task stopped before it finished. Try again.", now, agent, now - STALE_SECONDS))
    row = db.execute("""SELECT id,paths,sha256,device,runtime_meta FROM delete_jobs WHERE agent=? AND state='queued'
        ORDER BY created_at LIMIT 1""", (agent,)).fetchone()
    if not row or db.execute("UPDATE delete_jobs SET state='running',updated_at=? WHERE id=? AND state='queued'",
                             (int(time.time()), row[0])).rowcount != 1:
        return None
    job = {"id": row[0], "paths": json.loads(row[1]), "sha256": row[2]}
    if row[3].endswith(':unsloth'):
        job.update(device=row[3], runtime_meta=json.loads(row[4]))
    return job


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
