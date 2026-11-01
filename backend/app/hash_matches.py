"""Opt-in local SHA-256 verification jobs; metadata only, no model-file transfers."""
import hmac
import re
import sqlite3
import time
import uuid
from fastapi import HTTPException
from .hf_repository import list_model_files

SHA = re.compile(r"^[0-9a-fA-F]{64}$")
TERMINAL = ("verified", "mismatch", "failed", "stale")
MAX_PENDING = 12

def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS hash_jobs (
        id TEXT PRIMARY KEY, device TEXT NOT NULL, path TEXT NOT NULL,
        repository TEXT NOT NULL, remote_path TEXT NOT NULL,
        remote_sha TEXT NOT NULL, remote_size INTEGER NOT NULL,
        local_size INTEGER NOT NULL, local_modified_ns INTEGER NOT NULL,
        state TEXT NOT NULL, result_sha TEXT, error TEXT,
        created_at INTEGER NOT NULL, claimed_at INTEGER, completed_at INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS selected_matches (
        device TEXT NOT NULL, path TEXT NOT NULL, repository TEXT NOT NULL,
        remote_path TEXT NOT NULL, sha256 TEXT NOT NULL, selected_at INTEGER NOT NULL,
        PRIMARY KEY(device,path))""")

def _inventory_row(db, device, path):
    return db.execute("SELECT size_bytes,modified_ns,kind FROM models WHERE device=? AND path=?",
                      (device, path)).fetchone()

def _remote_file(repo, remote_path):
    if len(remote_path) > 1024 or not remote_path or remote_path.startswith("/"):
        raise HTTPException(422, "Invalid remote file path")
    listing = list_model_files(repo)
    for item in listing["files"]:
        if item["path"] == remote_path:
            return item
    raise HTTPException(404, "Remote model file not found in repository listing")

def queue_job(db, device, path, repository, remote_path):
    remote = _remote_file(repository, remote_path)
    if remote["sha256"] is None or remote["size_bytes"] is None:
        raise HTTPException(422, "Provider has no SHA-256 and size for this file")
    if not device.endswith(":comfyui"):
        raise HTTPException(422,"Only ComfyUI inventory files can be hashed")
    row = _inventory_row(db, device, path)
    if not row or row[2] == "ollama":
        raise HTTPException(404, "Local model not found")
    size, modified, kind = row
    if size != remote["size_bytes"]:
        raise HTTPException(422, "File sizes differ; not the same version")
    # Do not conflate merely sharing a filename with a verified hash.
    existing = db.execute("""SELECT id,state FROM hash_jobs
        WHERE device=? AND path=? AND repository=? AND remote_path=?
        AND local_size=? AND local_modified_ns=? AND remote_sha=?
        AND state IN ('queued','running','verified') ORDER BY created_at DESC LIMIT 1""",
        (device,path,repository,remote_path,size,modified,remote["sha256"])).fetchone()
    if existing:
        return {"id": existing[0], "state": existing[1]}
    pending = db.execute("SELECT count(*) FROM hash_jobs WHERE device=? AND state IN ('queued','running')",(device,)).fetchone()[0]
    if pending >= MAX_PENDING:
        raise HTTPException(429, "Too many pending checks for this device")
    now = int(time.time())
    job_id = uuid.uuid4().hex
    db.execute("""INSERT INTO hash_jobs
        (id,device,path,repository,remote_path,remote_sha,remote_size,
        local_size,local_modified_ns,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',?)""",
        (job_id,device,path,repository,remote_path,remote["sha256"],remote["size_bytes"],size,modified,now))
    return {"id": job_id, "state": "queued"}

def claim_job(db, device):
    now = int(time.time())
    # Abandoned jobs become queued again, allowing agent restarts.
    db.execute("UPDATE hash_jobs SET state='queued',claimed_at=NULL WHERE device=? AND state='running' AND claimed_at<?",
               (device, now-3600))
    row = db.execute("""SELECT id,path,local_size,local_modified_ns FROM hash_jobs
        WHERE device=? AND state='queued' ORDER BY created_at,id LIMIT 1""",(device,)).fetchone()
    if not row:
        return None
    if db.execute("UPDATE hash_jobs SET state='running',claimed_at=? WHERE id=? AND state='queued'",(now,row[0])).rowcount != 1:
        return None
    return dict(zip(("id","path","size_bytes","modified_ns"),row))

def finish_job(db, job_id, device, digest, error):
    row = db.execute("""SELECT path,local_size,local_modified_ns,remote_sha,remote_size
        FROM hash_jobs WHERE id=? AND device=? AND state='running'""",(job_id,device)).fetchone()
    if not row:
        raise HTTPException(409, "Job is no longer running for this device")
    path,size,modified,remote_sha,remote_size = row
    current = _inventory_row(db,device,path)
    if current is None or (current[0],current[1]) != (size,modified):
        state="stale"
    elif error:
        state="failed"
    elif not digest or not SHA.fullmatch(digest):
        raise HTTPException(422, "Expected a SHA-256 digest or error")
    else:
        state="verified" if size == remote_size and hmac.compare_digest(digest.lower(),remote_sha.lower()) else "mismatch"
    db.execute("""UPDATE hash_jobs SET state=?,result_sha=?,error=?,completed_at=?
                  WHERE id=? AND device=?""",
               (state,digest.lower() if digest and SHA.fullmatch(digest) else None,str(error)[:300] if error else None,int(time.time()),job_id,device))
    return {"id":job_id,"state":state}

def get_job(db, job_id):
    row = db.execute("""SELECT id,device,path,repository,remote_path,state,error,created_at
                        FROM hash_jobs WHERE id=?""",(job_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Unknown hash verification job")
    return dict(zip(("id","device","path","repository","remote_path","state","error","created_at"),row))

def select_match(db, job_id):
    row=db.execute("""SELECT device,path,repository,remote_path,remote_sha,remote_size,
        local_size,local_modified_ns,state FROM hash_jobs WHERE id=?""",(job_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Unknown verification job")
    device,path,repo,remote_path,sha,rsize,lsize,modified,state=row
    if state!="verified":
        raise HTTPException(409,"The file must have a verified SHA-256 match before selection")
    current=_inventory_row(db,device,path)
    if not current or (current[0],current[1]) != (lsize,modified):
        raise HTTPException(409,"Local inventory changed; verify again")
    # Confirm the current provider listing still advertises this exact artifact.
    remote=_remote_file(repo,remote_path)
    if remote["sha256"] != sha or remote["size_bytes"] != rsize:
        raise HTTPException(409,"Provider metadata changed; verify again")
    db.execute("""INSERT INTO selected_matches (device,path,repository,remote_path,sha256,selected_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(device,path) DO UPDATE SET
        repository=excluded.repository,remote_path=excluded.remote_path,
        sha256=excluded.sha256,selected_at=excluded.selected_at""",
        (device,path,repo,remote_path,sha,int(time.time())))
    return {"device":device,"path":path,"repository":repo,"remote_path":remote_path,"sha256":sha,"selected":True}

def selected_for_inventory(db):
    rows=db.execute("""SELECT s.device,s.path,s.repository,s.remote_path,s.sha256
    FROM selected_matches s JOIN models m ON s.device=m.device AND s.path=m.path""").fetchall()
    return [dict(zip(("device","path","repository","remote_path","sha256"),r)) for r in rows]
