"""User-approved update downloads. The NAS decides what to fetch and the expected SHA-256;
the Windows agent downloads next to the old file and never overwrites it. The old file is
deleted only when the user asks after a verified download (see request_cleanup)."""
import json
import re
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import quote
from fastapi import HTTPException
from .intelligence import physical_device
from .sources import annotate

SAFE_NAME = re.compile(r"^[\w .()+,-]{1,200}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx)$")
ACTIVE = ("queued", "running")
STALE_SECONDS = 1800
FIELDS = ("id", "device", "path", "provider", "file_name", "size_bytes", "state", "bytes_done",
          "error", "result_path", "created_at", "updated_at", "cleanup", "cleanup_error", "target_folder", "source_ref")

def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS download_jobs (
        id TEXT PRIMARY KEY, agent TEXT NOT NULL, device TEXT NOT NULL, path TEXT NOT NULL,
        provider TEXT NOT NULL, url TEXT, file_name TEXT NOT NULL, expected_sha TEXT,
        size_bytes INTEGER, state TEXT NOT NULL, bytes_done INTEGER NOT NULL DEFAULT 0,
        error TEXT, result_path TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(download_jobs)")]
    # cleanup: NULL, then queued -> running -> done | failed when the user deletes the old file.
    # target_folder/source_ref: new downloads from Browse, which have no old file next to them.
    for column in ("old_sha", "cleanup", "cleanup_error", "target_folder", "source_ref"):
        if column not in columns:
            db.execute(f"ALTER TABLE download_jobs ADD COLUMN {column} TEXT")

def _record(db, device, path):
    row = db.execute("SELECT device,path,name,kind,size_bytes,modified_ns,sha256 FROM models WHERE device=? AND path=?",
                     (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    return annotate(db, [dict(zip(("device", "path", "name", "kind", "size_bytes", "modified_ns", "sha256"), row))])[0]

def _plan(db, record):
    """Return (url, file_name, expected_sha, size) for the record's update, built from fixed hosts only."""
    update = record["update"]
    if update["provider"] == "civitai":
        stored = db.execute("SELECT latest_file FROM civitai_files WHERE sha256=?", (record["sha256"],)).fetchone()
        file = json.loads(stored[0]) if stored and stored[0] else None
        if not file or not file.get("sha256"):
            raise HTTPException(409, "Civitai did not publish a hash for the new file; download it manually")
        url = f"https://civitai.com/api/download/models/{int(update['version_id'])}?fileId={int(file['id'])}"
        return url, file["name"], file["sha256"], file["size_bytes"]
    if update["provider"] == "huggingface":
        repo, remote_path, latest = db.execute("""SELECT repository,remote_path,latest_sha FROM selected_matches
            WHERE device=? AND path=?""", (record["device"], record["path"])).fetchone()
        name = PurePosixPath(remote_path)
        # Same filename upstream, so tag the new copy with its hash instead of colliding with the old file.
        file_name = f"{name.stem}.{latest[:8]}{name.suffix}"
        url = "https://huggingface.co/" + quote(repo, safe="/") + "/resolve/main/" + quote(remote_path, safe="/")
        return url, file_name, latest, None
    return None, record["name"], None, None  # Ollama: the agent asks local Ollama to pull the tag

def create(db, device, path):
    existing = db.execute(f"SELECT {','.join(FIELDS)} FROM download_jobs WHERE device=? AND path=? AND state IN (?,?)",
                          (device, path, *ACTIVE)).fetchone()
    if existing:
        return dict(zip(FIELDS, existing))
    record = _record(db, device, path)
    if not record["update"]:
        raise HTTPException(409, "No update is available for this model")
    url, file_name, expected, size = _plan(db, record)
    if url and not SAFE_NAME.fullmatch(file_name or ""):
        raise HTTPException(422, "Unsafe file name from provider")
    now = int(time.time())
    job = {"id": uuid.uuid4().hex, "device": device, "path": path, "provider": record["update"]["provider"],
           "file_name": file_name, "size_bytes": size, "state": "queued", "bytes_done": 0, "error": None,
           "result_path": None, "created_at": now, "updated_at": now}
    db.execute("""INSERT INTO download_jobs (id,agent,device,path,provider,url,file_name,expected_sha,size_bytes,
        state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?)""",
        (job["id"], physical_device(device), device, path, job["provider"], url, file_name, expected, size, now, now))
    return job

def claim(db, agent):
    now = int(time.time())
    # A download whose agent stopped reporting progress is handed out again.
    db.execute("UPDATE download_jobs SET state='queued' WHERE agent=? AND state='running' AND updated_at<?",
               (agent, now - STALE_SECONDS))
    row = db.execute("""SELECT id,device,path,provider,url,file_name,expected_sha,size_bytes,target_folder FROM download_jobs
        WHERE agent=? AND state='queued' ORDER BY created_at LIMIT 1""", (agent,)).fetchone()
    if not row or db.execute("UPDATE download_jobs SET state='running',bytes_done=0,updated_at=? WHERE id=? AND state='queued'",
                             (now, row[0])).rowcount != 1:
        return None
    return dict(zip(("id", "device", "path", "provider", "url", "file_name", "expected_sha", "size_bytes", "target_folder"), row))

def progress(db, job_id, agent, bytes_done):
    if db.execute("UPDATE download_jobs SET bytes_done=?,updated_at=? WHERE id=? AND agent=? AND state='running'",
                  (bytes_done, int(time.time()), job_id, agent)).rowcount != 1:
        raise HTTPException(409, "Download is no longer running for this agent")
    return {"ok": True}

def finish(db, job_id, agent, sha256, error, result_path):
    row = db.execute("SELECT expected_sha FROM download_jobs WHERE id=? AND agent=? AND state='running'",
                     (job_id, agent)).fetchone()
    if not row:
        raise HTTPException(409, "Download is no longer running for this agent")
    if not error and row[0] and (sha256 or "").lower() != row[0]:
        error = "Downloaded file hash did not match the provider"
    state = "failed" if error else "done"
    db.execute("UPDATE download_jobs SET state=?,error=?,result_path=?,updated_at=? WHERE id=?",
               (state, str(error)[:300] if error else None, None if error else result_path, int(time.time()), job_id))
    if state == "done":
        _request_scan(db, agent)  # list the new file without waiting for the scheduled sync
    return {"id": job_id, "state": state}

def recent(db, limit=100):
    rows = db.execute(f"SELECT {','.join(FIELDS)} FROM download_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(zip(FIELDS, row)) for row in rows]

def request_cleanup(db, job_id):
    """Queue deletion of the old file after a verified download. The agent re-checks both files first."""
    row = db.execute("SELECT device,path,provider,state,result_path,cleanup FROM download_jobs WHERE id=?",
                     (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown download")
    device, path, provider, state, result_path, cleanup = row
    if provider == "ollama":
        raise HTTPException(409, "Ollama replaces the old build itself")
    if state != "done" or not result_path:
        raise HTTPException(409, "The new version must finish downloading and pass its hash check first")
    if cleanup in ("queued", "running", "done"):
        return {"id": job_id, "cleanup": cleanup}
    current = db.execute("SELECT sha256 FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not current or not current[0]:
        raise HTTPException(409, "The old file is no longer in the inventory")
    # Pin the exact old file: the agent deletes nothing whose hash differs from this.
    db.execute("UPDATE download_jobs SET cleanup='queued',cleanup_error=NULL,old_sha=?,updated_at=? WHERE id=?",
               (current[0], int(time.time()), job_id))
    return {"id": job_id, "cleanup": "queued"}

def claim_cleanup(db, agent):
    row = db.execute("""SELECT id,path,result_path,old_sha,expected_sha FROM download_jobs
        WHERE agent=? AND cleanup='queued' ORDER BY updated_at LIMIT 1""", (agent,)).fetchone()
    if not row or db.execute("UPDATE download_jobs SET cleanup='running' WHERE id=? AND cleanup='queued'",
                             (row[0],)).rowcount != 1:
        return None
    return dict(zip(("id", "path", "new_path", "old_sha", "new_sha"), row))

def finish_cleanup(db, job_id, agent, error):
    if db.execute("""UPDATE download_jobs SET cleanup=?,cleanup_error=?,updated_at=?
        WHERE id=? AND agent=? AND cleanup='running'""",
        ("failed" if error else "done", str(error)[:300] if error else None, int(time.time()), job_id, agent)).rowcount != 1:
        raise HTTPException(409, "Cleanup is no longer running for this agent")
    if not error:
        _request_scan(db, agent)  # the dashboard drops the deleted file
    return {"id": job_id, "cleanup": "failed" if error else "done"}

def _request_scan(db, agent):
    db.execute("""INSERT INTO agent_status (device,phase,scan_requested_at) VALUES (?,'idle',?)
        ON CONFLICT(device) DO UPDATE SET scan_requested_at=excluded.scan_requested_at""", (agent, int(time.time())))

def create_new(db, agent, url, file_name, expected_sha, size_bytes, folder, source_ref, provider):
    """Queue a download of a model that is not on the PC yet, into a fixed ComfyUI folder."""
    from .browse import valid_folder
    if not valid_folder(folder) or not SAFE_NAME.fullmatch(file_name or "") or not expected_sha:
        raise HTTPException(422, "Unsupported folder, unsafe file name or no published hash")
    existing = db.execute(f"SELECT {','.join(FIELDS)} FROM download_jobs WHERE source_ref=? AND agent=? AND state IN (?,?)",
                          (source_ref, agent, *ACTIVE)).fetchone()
    if existing:
        return dict(zip(FIELDS, existing))
    now = int(time.time())
    job_id = uuid.uuid4().hex
    db.execute("""INSERT INTO download_jobs (id,agent,device,path,provider,url,file_name,expected_sha,size_bytes,
        state,created_at,updated_at,target_folder,source_ref) VALUES (?,?,?,'',?,?,?,?,?,'queued',?,?,?,?)""",
        (job_id, agent, agent + ":comfyui", provider, url, file_name, expected_sha, size_bytes, now, now, folder, source_ref))
    return dict(zip(FIELDS, db.execute(f"SELECT {','.join(FIELDS)} FROM download_jobs WHERE id=?", (job_id,)).fetchone()))
