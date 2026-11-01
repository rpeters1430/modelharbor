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
from .sources import annotate, ensure_tables as ensure_source_tables, start_worker
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
        rows = db.execute("SELECT device,last_seen,comfyui_count,ollama_count FROM agent_heartbeats ORDER BY device").fetchall()
    now = int(time.time())
    return [{"device": r[0], "last_seen": r[1], "online": now-r[1] <= 3900,
             "comfyui_count":r[2], "ollama_count":r[3]} for r in rows]

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
