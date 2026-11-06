"""Shrink a model that does not fit the GPU to ComfyUI's int8_convrot format, on its PC, when the user asks.

The agent re-hashes the source, converts with ComfyUI's own Python and quantizer into a new file next to it, and
never changes the source. The new file inherits the source's identity (Civitai or Hugging Face link)."""
import time
import uuid
from fastapi import HTTPException
from .downloads import _request_scan
from .fit import COMPACT

FIELDS = ("id", "agent", "device", "path", "sha256", "state", "error", "result_path", "result_sha",
          "result_size", "created_at", "updated_at")
# Diffusion models and text encoders only: their transformer blocks are what int8_convrot is made for.
KINDS = {"diffusion", "text_encoder"}



def ensure_tables(db):
    # state: queued -> running -> done | failed
    db.execute("""CREATE TABLE IF NOT EXISTS convert_jobs (
        id TEXT PRIMARY KEY, agent TEXT NOT NULL, device TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
        state TEXT NOT NULL, error TEXT, result_path TEXT, result_sha TEXT, result_size INTEGER,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")


def ineligible(name, kind):
    """Why this file cannot be converted, or None."""
    if kind not in KINDS:
        return "Only diffusion models and text encoders can be converted"
    if not name.lower().endswith(".safetensors"):
        return "Only .safetensors files can be converted"
    if COMPACT.search(name):
        return "This file is already 8-bit or smaller"
    return None


def get(db, job_id):
    row = db.execute(f"SELECT {','.join(FIELDS)} FROM convert_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown conversion")
    return dict(zip(FIELDS, row))


def recent(db, limit=30):
    return [dict(zip(FIELDS, r)) for r in db.execute(
        f"SELECT {','.join(FIELDS)} FROM convert_jobs ORDER BY created_at DESC LIMIT ?", (limit,))]


def request(db, device, path):
    if not device.endswith(":comfyui"):
        raise HTTPException(422, "Only ComfyUI model files can be converted")
    row = db.execute("SELECT name,kind,sha256 FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    name, kind, sha = row
    if reason := ineligible(name, kind):
        raise HTTPException(422, reason)
    if not sha:
        raise HTTPException(409, "This file has not been hashed yet; wait for the next Windows sync")
    active = db.execute(f"""SELECT {','.join(FIELDS)} FROM convert_jobs WHERE device=? AND sha256=?
        AND state IN ('queued','running')""", (device, sha)).fetchone()
    if active:
        return dict(zip(FIELDS, active))
    now, job_id = int(time.time()), uuid.uuid4().hex
    db.execute("""INSERT INTO convert_jobs (id,agent,device,path,sha256,state,created_at,updated_at)
        VALUES (?,?,?,?,?,'queued',?,?)""", (job_id, device.removesuffix(":comfyui"), device, path, sha, now, now))
    return get(db, job_id)


# The agent stops a conversion after 3 hours, so one running much longer was cut off by a crash or restart.
STALE_SECONDS = 4 * 3600
INTERRUPTED = "Interrupted: the PC's background task stopped before it finished. Try again."


def claim(db, agent):
    now = int(time.time())
    db.execute("UPDATE convert_jobs SET state='failed',error=?,updated_at=? WHERE agent=? AND state='running' AND updated_at<?",
               (INTERRUPTED, now, agent, now - STALE_SECONDS))
    row = db.execute("""SELECT id,path,sha256 FROM convert_jobs WHERE agent=? AND state='queued'
        ORDER BY created_at LIMIT 1""", (agent,)).fetchone()
    if not row or db.execute("UPDATE convert_jobs SET state='running',updated_at=? WHERE id=? AND state='queued'",
                             (int(time.time()), row[0])).rowcount != 1:
        return None
    return {"id": row[0], "path": row[1], "sha256": row[2]}


def inherit_source(db, source_sha, device, path, sha, size, modified_ns):
    """Give the converted file the source's Civitai or Hugging Face identity, marked as not the same file."""
    columns = [r[1] for r in db.execute("PRAGMA table_info(civitai_files)") if r[1] not in ("sha256", "verified")]
    db.execute(f"""INSERT OR REPLACE INTO civitai_files (sha256,verified,{','.join(columns)})
        SELECT ?,0,{','.join(columns)} FROM civitai_files WHERE sha256=? AND found=1""", (sha, source_sha))
    # Hugging Face links are per path, matched by size and time once the scan sees the new file.
    columns = [r[1] for r in db.execute("PRAGMA table_info(selected_matches)")
               if r[1] not in ("device", "path", "local_size", "local_modified_ns", "selected_at")]
    db.execute(f"""INSERT OR REPLACE INTO selected_matches (device,path,local_size,local_modified_ns,selected_at,{','.join(columns)})
        SELECT ?,?,?,?,?,{','.join(columns)} FROM selected_matches s
        WHERE s.rowid=(SELECT s2.rowid FROM selected_matches s2 JOIN models m ON s2.device=m.device AND s2.path=m.path
                       WHERE m.sha256=? LIMIT 1)""", (device, path, size, modified_ns, int(time.time()), source_sha))


def finish(db, job_id, agent, error, result=None):
    job = get(db, job_id)
    if job["agent"] != agent or job["state"] != "running":
        raise HTTPException(409, "This conversion is no longer running for this agent")
    if not error and not result:
        error = "The agent reported no converted file"
    now = int(time.time())
    if error:
        db.execute("UPDATE convert_jobs SET state='failed',error=?,updated_at=? WHERE id=?", (str(error)[:300], now, job_id))
        return get(db, job_id)
    db.execute("""UPDATE convert_jobs SET state='done',error=NULL,result_path=?,result_sha=?,result_size=?,updated_at=?
        WHERE id=?""", (result["path"], result["sha256"], result["size_bytes"], now, job_id))
    inherit_source(db, job["sha256"], job["device"], result["path"], result["sha256"], result["size_bytes"],
                   result["modified_ns"])
    _request_scan(db, agent)
    return get(db, job_id)
