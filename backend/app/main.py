"""ModelHarbor read-only API foundation."""
import hmac
import os
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .intelligence import enrich
from .providers import search as provider_search
from .hf_repository import list_model_files
from . import downloads
from .sources import annotate, civitai_link, civitai_unlink, civitai_versions, ensure_tables as ensure_source_tables, start_worker
from .hash_matches import ensure_tables, queue_job, claim_job, finish_job, get_job, select_match, selected_for_inventory

DB = Path(os.environ.get("MODELHARBOR_DB", "/data/modelharbor.sqlite3"))
TOKEN = os.environ.get("MODELHARBOR_AGENT_TOKEN", "")

@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("MODELHARBOR_SOURCE_CHECKS", "1") != "0":
        start_worker(connect, list_model_files)
    yield

app = FastAPI(title="ModelHarbor", version="0.1.0", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.middleware("http")
async def revalidate_dashboard(request, call_next):
    response = await call_next(request)
    # Browsers otherwise keep running an old dashboard after an image update; ETags keep this cheap.
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

class ModelRecord(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    file_id: str | None = Field(default=None, max_length=64)
    # File SHA-256 for ComfyUI files; registry manifest digest for Ollama models.
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

class Heartbeat(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    comfyui_count: int = Field(ge=0)
    ollama_count: int | None = Field(default=None, ge=0)

class Inventory(BaseModel):
    device: str
    models: list[ModelRecord]

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS models (
        device TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
        kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
        PRIMARY KEY (device, path))""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(models)")]
    for column in ("file_id", "sha256"):
        if column not in columns:
            db.execute(f"ALTER TABLE models ADD COLUMN {column} TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_heartbeats
        (device TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,
         comfyui_count INTEGER NOT NULL, ollama_count INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_status (
        device TEXT PRIMARY KEY, phase TEXT NOT NULL, done INTEGER, total INTEGER,
        done_bytes INTEGER, total_bytes INTEGER, current TEXT, message TEXT,
        started_at INTEGER, updated_at INTEGER, worker_seen INTEGER, scan_requested_at INTEGER)""")
    ensure_tables(db)
    ensure_source_tables(db)
    downloads.ensure_tables(db)
    db.commit()
    return db

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/models")
def models(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        fields = ("device","path","name","kind","size_bytes","modified_ns","file_id","sha256")
        rows = db.execute(f"SELECT {','.join(fields)} FROM models ORDER BY device,kind,name").fetchall()
        return enrich(annotate(db, [dict(zip(fields, row)) for row in rows]))

def authorize(value):
    if not TOKEN or not hmac.compare_digest((value or "").encode(), TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/api/inventory")
def inventory(payload: Inventory, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    if len(payload.models) > 100000 or any(m.device != payload.device for m in payload.models):
        raise HTTPException(status_code=422, detail="Invalid inventory")
    with closing(connect()) as db:
        with db:
            db.execute("DELETE FROM models WHERE device=?", (payload.device,))
            db.executemany("INSERT INTO models (device,path,name,kind,size_bytes,modified_ns,file_id,sha256) VALUES (?,?,?,?,?,?,?,?)",
                [(m.device,m.path,m.name,m.kind,m.size_bytes,m.modified_ns,m.file_id,m.sha256) for m in payload.models])
    return {"device": payload.device, "count": len(payload.models)}

@app.post("/api/agents/heartbeat")
def heartbeat(payload: Heartbeat, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            db.execute("""INSERT INTO agent_heartbeats VALUES (?,?,?,?)
                ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen,
                comfyui_count=excluded.comfyui_count,ollama_count=excluded.ollama_count""",
                (payload.device, int(time.time()), payload.comfyui_count, payload.ollama_count))
    return {"ok": True}

@app.get("/api/agents")
def agents(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        rows = db.execute("""SELECT h.device,h.last_seen,h.comfyui_count,h.ollama_count,
            s.phase,s.done,s.total,s.done_bytes,s.total_bytes,s.current,s.message,s.started_at,s.updated_at,
            s.worker_seen,s.scan_requested_at
            FROM agent_heartbeats h LEFT JOIN agent_status s ON s.device=h.device ORDER BY h.device""").fetchall()
    now = int(time.time())
    result = []
    for r in rows:
        worker_seen = r[13] or 0
        status = dict(zip(("phase", "done", "total", "done_bytes", "total_bytes", "current", "message",
                           "started_at", "updated_at"), r[4:13])) if r[12] else None  # None until a sync reports
        result.append({"device": r[0], "last_seen": r[1], "online": now - max(r[1], worker_seen) <= 3900,
            "comfyui_count": r[2], "ollama_count": r[3], "status": status,
            # The worker polls every ~20 s, so two minutes of silence means it is not running.
            "worker_running": now - worker_seen <= 120, "worker_seen": worker_seen or None,
            "scan_requested": bool(r[14])})
    return result

class AgentStatus(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    phase: str = Field(pattern=r"^(scanning|hashing|uploading|idle|error)$")
    done: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    done_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    current: str | None = Field(default=None, max_length=1024)
    message: str | None = Field(default=None, max_length=300)

@app.post("/api/agents/status")
def agent_status(payload: AgentStatus, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    now = int(time.time())
    with closing(connect()) as db:
        with db:
            db.execute("""INSERT INTO agent_status (device,phase,done,total,done_bytes,total_bytes,current,message,
                started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET
                phase=excluded.phase,done=excluded.done,total=excluded.total,done_bytes=excluded.done_bytes,
                total_bytes=excluded.total_bytes,current=excluded.current,message=excluded.message,
                started_at=CASE WHEN excluded.phase='scanning' AND agent_status.phase!='scanning'
                    THEN excluded.started_at ELSE COALESCE(agent_status.started_at,excluded.started_at) END,
                updated_at=excluded.updated_at""",
                (payload.device, payload.phase, payload.done, payload.total, payload.done_bytes,
                 payload.total_bytes, payload.current, payload.message, now, now))
    return {"ok": True}

@app.post("/api/agents/{device}/scan", status_code=202)
def request_scan(device: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            if not db.execute("SELECT 1 FROM agent_heartbeats WHERE device=?", (device,)).fetchone():
                raise HTTPException(404, "Unknown device")
            db.execute("""INSERT INTO agent_status (device,phase,scan_requested_at) VALUES (?,'idle',?)
                ON CONFLICT(device) DO UPDATE SET scan_requested_at=excluded.scan_requested_at""",
                (device, int(time.time())))
    return {"ok": True}

@app.get("/api/agents/{device}/scan-request")
def take_scan_request(device: str, x_modelharbor_token: str | None = Header(default=None)):
    """Polled by the agent's background worker; also records that the worker is alive."""
    authorize(x_modelharbor_token)
    now = int(time.time())
    with closing(connect()) as db:
        with db:
            row = db.execute("SELECT scan_requested_at FROM agent_status WHERE device=?", (device,)).fetchone()
            db.execute("""INSERT INTO agent_status (device,phase,worker_seen) VALUES (?,'idle',?)
                ON CONFLICT(device) DO UPDATE SET worker_seen=excluded.worker_seen,scan_requested_at=NULL""",
                (device, now))
    return {"scan": bool(row and row[0])}

@app.get("/api/discovery/{provider}")
def discover(provider: str, q: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    return {"provider": provider, "query": q, "verified": False,
            "results": provider_search(provider, q)}

@app.get("/api/huggingface/repository")
def huggingface_repository(repo: str, q: str = "", x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    return list_model_files(repo, q)

class VerifyRequest(BaseModel):
    device: str = Field(min_length=1,max_length=100)
    path: str = Field(min_length=1,max_length=4096)
    repository: str = Field(min_length=3,max_length=193)
    remote_path: str = Field(min_length=1,max_length=1024)

class HashReport(BaseModel):
    device: str = Field(min_length=1,max_length=100)
    sha256: str | None = Field(default=None,max_length=64)
    error: str | None = Field(default=None,max_length=300)

@app.post("/api/hash-jobs",status_code=202)
def create_hash_job(payload: VerifyRequest,x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            known = db.execute("SELECT sha256 FROM models WHERE device=? AND path=?",(payload.device,payload.path)).fetchone()
            # The agent already hashed this file during inventory, so no worker round trip is needed.
            return queue_job(db,payload.device,payload.path,payload.repository,payload.remote_path,known and known[0])

@app.get("/api/hash-jobs/next")
def next_hash_job(device: str,x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    if not 1 <= len(device) <= 100 or not device.endswith(":comfyui"):
        raise HTTPException(422,"Unsupported device")
    with closing(connect()) as db:
        with db:
            return {"job":claim_job(db,device)}

@app.post("/api/hash-jobs/{job_id}/result")
def report_hash(job_id: str,payload: HashReport,x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return finish_job(db,job_id,payload.device,payload.sha256,payload.error)

@app.get("/api/hash-jobs/{job_id}")
def hash_status(job_id: str,x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return get_job(db,job_id)

@app.post("/api/hash-jobs/{job_id}/select")
def confirm_hash_match(job_id: str,x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return select_match(db,job_id)

@app.get("/api/selected-matches")
def selected_matches(x_modelharbor_token: str | None=Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return selected_for_inventory(db)

class DownloadRequest(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

class DownloadProgress(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    bytes_done: int = Field(ge=0)

class DownloadResult(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = Field(default=None, max_length=300)
    result_path: str | None = Field(default=None, max_length=4096)

@app.post("/api/downloads", status_code=202)
def create_download(payload: DownloadRequest, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.create(db, payload.device, payload.path)

@app.get("/api/downloads")
def list_downloads(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return downloads.recent(db)

@app.get("/api/downloads/next")
def next_download(agent: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return {"job": downloads.claim(db, agent)}

@app.post("/api/downloads/{job_id}/progress")
def download_progress(job_id: str, payload: DownloadProgress, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.progress(db, job_id, payload.agent, payload.bytes_done)

@app.post("/api/downloads/{job_id}/result")
def download_result(job_id: str, payload: DownloadResult, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.finish(db, job_id, payload.agent, payload.sha256, payload.error, payload.result_path)

class CivitaiLink(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)
    model_id: int | None = Field(default=None, ge=1)
    version_id: int | None = Field(default=None, ge=1)
    file_id: int | None = Field(default=None, ge=1)

def _local_sha(db, device, path):
    row = db.execute("SELECT sha256 FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    if not row[0]:
        raise HTTPException(409, "This file has not been hashed yet; wait for the next Windows sync")
    return row[0]

@app.get("/api/civitai/models/{model_id}")
def civitai_model_versions(model_id: int, device: str, path: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        sha = _local_sha(db, device, path)
    result = civitai_versions(model_id, sha)
    if not result:
        raise HTTPException(404, "Civitai model not found")
    return result

@app.post("/api/civitai/link")
def link_civitai(payload: CivitaiLink, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    if not (payload.model_id and payload.version_id and payload.file_id):
        raise HTTPException(422, "Choose a model, version and file")
    with closing(connect()) as db:
        with db:
            result = civitai_link(db, _local_sha(db, payload.device, payload.path),
                                  payload.model_id, payload.version_id, payload.file_id)
    if not result:
        raise HTTPException(404, "That file is not part of the chosen Civitai version")
    return result

@app.post("/api/civitai/unlink")
def unlink_civitai(payload: CivitaiLink, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            civitai_unlink(db, _local_sha(db, payload.device, payload.path))
    return {"ok": True}

class CleanupResult(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    error: str | None = Field(default=None, max_length=300)

@app.post("/api/downloads/{job_id}/delete-old", status_code=202)
def delete_old_version(job_id: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.request_cleanup(db, job_id)

@app.get("/api/downloads/cleanup-next")
def next_cleanup(agent: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return {"job": downloads.claim_cleanup(db, agent)}

@app.post("/api/downloads/{job_id}/cleanup-result")
def cleanup_result(job_id: str, payload: CleanupResult, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.finish_cleanup(db, job_id, payload.agent, payload.error)
