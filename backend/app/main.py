"""ModelHarbor read-only API foundation."""
import hmac
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path
from pathlib import PurePosixPath
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .intelligence import enrich
from .providers import search as provider_search
from urllib.parse import quote
from .hf_repository import list_model_files, validate_repo
from .hf_inspect import inspect_huggingface
from .suggest_matches import suggest_matches, link_huggingface
from . import browse, converts, deletes, downloads, fit, civitai_credentials
from .sources import _civitai_model, _file_sha, fetch
from .sources import annotate, ignore_key, civitai_check, civitai_link, civitai_unlink, civitai_versions, ensure_tables as ensure_source_tables, start_worker
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

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Served from the root so its scope covers the whole dashboard.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

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
    # SHA-256 of a safetensors file's tensor data only (Civitai AutoV3), unchanged by metadata edits.
    weights_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    trigger_words: list[str] | str | None = None
    base_model_guess: str | None = Field(default=None, max_length=100)
    meta_title: str | None = Field(default=None, max_length=250)
    # LLM servers: where the model came from (Lemonade "owner/repo:variant" or a local path).
    checkpoint: str | None = Field(default=None, max_length=1024)
    update_available: bool | None = None
    runtime_meta: dict | None = None

class Hardware(BaseModel):
    gpu: str | None = Field(default=None, max_length=120)
    vendor: str | None = Field(default=None, pattern=r"^(nvidia|amd|intel|other)$")
    vram_bytes: int | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)

class LemonadeInfo(BaseModel):
    host: str | None = Field(default=None, max_length=253)
    gpu: str | None = Field(default=None, max_length=120)
    vendor: str | None = Field(default=None, pattern=r"^(nvidia|amd|intel|other)$")
    vram_bytes: int | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)

class LMStudioInfo(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(connected|unavailable|disabled)$")
    error: str | None = Field(default=None, max_length=250)
    host: str | None = Field(default=None, max_length=253)
    gpu: str | None = Field(default=None, max_length=120)
    vendor: str | None = Field(default=None, pattern=r"^(nvidia|amd|intel|other)$")
    vram_bytes: int | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)

class Heartbeat(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    comfyui_count: int = Field(ge=0)
    ollama_count: int | None = Field(default=None, ge=0)
    lemonade_count: int | None = Field(default=None, ge=0)
    lemonade: LemonadeInfo | None = None  # the Lemonade PC, which may not be the agent's PC
    lmstudio_count: int | None = Field(default=None, ge=0)
    lmstudio: LMStudioInfo | None = None
    hardware: Hardware | None = None

class Inventory(BaseModel):
    device: str
    models: list[ModelRecord]

# shortcut: database paths this process already set up; a file deleted while running needs a restart.
_migrated = set()

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    if DB not in _migrated:
        _migrate(db)
        _migrated.add(DB)
    return db

def _migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS models (
        device TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
        kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
        PRIMARY KEY (device, path))""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(models)")]
    for column in ("file_id", "sha256", "weights_sha256", "trigger_words", "base_model_guess", "meta_title",
                   "checkpoint", "update_available", "runtime_meta"):
        if column not in columns:
            db.execute(f"ALTER TABLE models ADD COLUMN {column} TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_heartbeats
        (device TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,
         comfyui_count INTEGER NOT NULL, ollama_count INTEGER)""")
    heartbeat_columns = [row[1] for row in db.execute("PRAGMA table_info(agent_heartbeats)")]
    if "hardware" not in heartbeat_columns:
        db.execute("ALTER TABLE agent_heartbeats ADD COLUMN hardware TEXT")  # JSON from the agent
    if "lemonade_count" not in heartbeat_columns:
        db.execute("ALTER TABLE agent_heartbeats ADD COLUMN lemonade_count INTEGER")
    if "lemonade" not in heartbeat_columns:
        db.execute("ALTER TABLE agent_heartbeats ADD COLUMN lemonade TEXT")  # JSON from the agent
    if "lmstudio_count" not in heartbeat_columns:
        db.execute("ALTER TABLE agent_heartbeats ADD COLUMN lmstudio_count INTEGER")
    if "lmstudio" not in heartbeat_columns:
        db.execute("ALTER TABLE agent_heartbeats ADD COLUMN lmstudio TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_status (
        device TEXT PRIMARY KEY, phase TEXT NOT NULL, done INTEGER, total INTEGER,
        done_bytes INTEGER, total_bytes INTEGER, current TEXT, message TEXT,
        started_at INTEGER, updated_at INTEGER, worker_seen INTEGER, scan_requested_at INTEGER)""")
    ensure_tables(db)
    ensure_source_tables(db)
    downloads.ensure_tables(db)
    civitai_credentials.ensure_tables(db)
    deletes.ensure_tables(db)
    converts.ensure_tables(db)
    db.commit()

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/models")
def models(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        fields = ("device","path","name","kind","size_bytes","modified_ns","file_id","sha256","trigger_words","base_model_guess","meta_title",
                  "checkpoint","update_available","runtime_meta")
        rows = db.execute(f"SELECT {','.join(fields)} FROM models ORDER BY device,kind,name").fetchall()
        hidden = _unreachable_runtimes(db)
        records = [dict(zip(fields, row)) for row in rows if row[0] not in hidden]
        for record in records:
            try:
                record["runtime_meta"] = json.loads(record["runtime_meta"]) if record["runtime_meta"] else None
            except (TypeError, ValueError):
                record["runtime_meta"] = None
        return enrich(annotate(db, records))

def _unreachable_runtimes(db):
    """Inventory devices ("pc:lemonade") of LLM servers the agent could not reach on its last sync.
    The agent keeps their old list so it returns when the server is back; until then it is not shown."""
    hidden = set()
    for device, ollama, lemonade, lmstudio in db.execute(
            "SELECT device,ollama_count,lemonade_count,lmstudio FROM agent_heartbeats"):
        if ollama is None:
            hidden.add(device + ":ollama")
        if lemonade is None:
            hidden.add(device + ":lemonade")
        # The LM Studio count is kept across failed syncs, so its reported status decides.
        try:
            if lmstudio and json.loads(lmstudio).get("status") != "connected":
                hidden.add(device + ":lmstudio")
        except (TypeError, ValueError, AttributeError):
            pass
    return hidden

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
            db.executemany("""INSERT INTO models (device,path,name,kind,size_bytes,modified_ns,file_id,sha256,weights_sha256,trigger_words,base_model_guess,meta_title,checkpoint,update_available,runtime_meta)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(m.device,m.path,m.name,m.kind,m.size_bytes,m.modified_ns,m.file_id,m.sha256,m.weights_sha256,json.dumps(m.trigger_words) if isinstance(m.trigger_words, list) else m.trigger_words,m.base_model_guess,m.meta_title,
                  m.checkpoint,None if m.update_available is None else int(m.update_available),
                  json.dumps(m.runtime_meta, separators=(",", ":")) if m.runtime_meta is not None else None) for m in payload.models])
            db.execute("""UPDATE selected_matches SET local_size=(SELECT size_bytes FROM models WHERE models.device=selected_matches.device AND models.path=selected_matches.path), local_modified_ns=(SELECT modified_ns FROM models WHERE models.device=selected_matches.device AND models.path=selected_matches.path) WHERE EXISTS (SELECT 1 FROM models WHERE models.device=selected_matches.device AND models.path=selected_matches.path)""")
    return {"device": payload.device, "count": len(payload.models)}

@app.post("/api/agents/heartbeat")
def heartbeat(payload: Heartbeat, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            hardware = payload.hardware and payload.hardware.model_dump_json()
            lemonade = payload.lemonade and payload.lemonade.model_dump_json()
            lmstudio = payload.lmstudio and payload.lmstudio.model_dump_json()
            db.execute("""INSERT INTO agent_heartbeats (device,last_seen,comfyui_count,ollama_count,lemonade_count,lemonade,hardware,lmstudio_count,lmstudio)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen,
                comfyui_count=excluded.comfyui_count,ollama_count=excluded.ollama_count,
                lemonade_count=excluded.lemonade_count,
                lemonade=COALESCE(excluded.lemonade,agent_heartbeats.lemonade),
                hardware=COALESCE(excluded.hardware,agent_heartbeats.hardware),
                lmstudio_count=COALESCE(excluded.lmstudio_count,agent_heartbeats.lmstudio_count),
                lmstudio=COALESCE(excluded.lmstudio,agent_heartbeats.lmstudio)""",
                (payload.device, int(time.time()), payload.comfyui_count, payload.ollama_count,
                 payload.lemonade_count, lemonade, hardware, payload.lmstudio_count, lmstudio))
    return {"ok": True}

@app.get("/api/agents")
def agents(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        rows = db.execute("""SELECT h.device,h.last_seen,h.comfyui_count,h.ollama_count,
            s.phase,s.done,s.total,s.done_bytes,s.total_bytes,s.current,s.message,s.started_at,s.updated_at,
            s.worker_seen,s.scan_requested_at,h.hardware,h.lemonade_count,h.lemonade,h.lmstudio_count,h.lmstudio
            FROM agent_heartbeats h LEFT JOIN agent_status s ON s.device=h.device ORDER BY h.device""").fetchall()
    now = int(time.time())
    result = []
    for r in rows:
        worker_seen = r[13] or 0
        status = dict(zip(("phase", "done", "total", "done_bytes", "total_bytes", "current", "message",
                           "started_at", "updated_at"), r[4:13])) if r[12] else None  # None until a sync reports
        result.append({"device": r[0], "last_seen": r[1], "online": now - max(r[1], worker_seen) <= 3900,
            "comfyui_count": r[2], "ollama_count": r[3], "lemonade_count": r[16],
            "lemonade": json.loads(r[17]) if r[17] else None, "status": status,
            "lmstudio_count": r[18], "lmstudio": json.loads(r[19]) if r[19] else None,
            # The worker polls every ~20 s, so two minutes of silence means it is not running.
            "worker_running": now - worker_seen <= 120, "worker_seen": worker_seen or None,
            "scan_requested": bool(r[14]), "hardware": json.loads(r[15]) if r[15] else None})
    return result

@app.delete("/api/agents/{device}")
def remove_agent(device: str, x_modelharbor_token: str | None = Header(default=None)):
    """Forget an offline PC and its app records; never request filesystem deletion."""
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            # Serialize the online check with heartbeats and worker polls.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT h.last_seen,COALESCE(s.worker_seen,0)
                FROM agent_heartbeats h LEFT JOIN agent_status s ON s.device=h.device
                WHERE h.device=?""", (device,)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown device")
            if int(time.time()) - max(row) <= 3900:
                raise HTTPException(409, "This device is online. Stop its agent before removing it.")
            sources = (device, *(device + ":" + runtime for runtime in
                                ("comfyui", "ollama", "lemonade", "lmstudio")))
            placeholders = ",".join("?" for _ in sources)
            removed = db.execute(f"DELETE FROM models WHERE device IN ({placeholders})", sources).rowcount
            for table in ("selected_matches", "hash_jobs"):
                db.execute(f"DELETE FROM {table} WHERE device IN ({placeholders})", sources)
            for table in ("download_jobs", "delete_jobs", "convert_jobs"):
                db.execute(f"DELETE FROM {table} WHERE agent=? OR device IN ({placeholders})", (device, *sources))
            db.execute("DELETE FROM agent_status WHERE device=?", (device,))
            db.execute("DELETE FROM agent_heartbeats WHERE device=?", (device,))
    return {"ok": True, "device": device, "models_removed": removed}

def _hardware(db, agent, column="hardware"):
    """The agent PC's hardware, or with column="lemonade"/"lmstudio" that LLM runtime's PC,
    as the agent last reported it."""
    row = db.execute(f"SELECT {column} FROM agent_heartbeats WHERE device=?", (agent,)).fetchone() if agent else None
    return json.loads(row[0]) if row and row[0] else None

def _llm_hardware(db, agent):
    """Hardware reported for a remote LLM runtime, when the agent can identify it."""
    return _hardware(db, agent, "lemonade") or _hardware(db, agent, "lmstudio")

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
def huggingface_repository(repo: str, q: str = "", agent: str = "", x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        shas, hw = browse.local_shas(db), _hardware(db, agent)
    return fit.annotate_listing(browse.mark_huggingface(list_model_files(repo, q), shas), hw)

@app.get("/api/huggingface/inspect")
def huggingface_inspect(url: str, agent: str = "", x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    try:
        data = inspect_huggingface(url)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, f"Hugging Face is unavailable right now: {exc}") from exc
    with closing(connect()) as db:
        shas, hw, llm_hw = browse.local_shas(db), _hardware(db, agent), _llm_hardware(db, agent)
        lmstudio_hw = _hardware(db, agent, "lmstudio")
    annotated = fit.annotate_listing(browse.mark_huggingface({"files": data["files"]}, shas), hw, llm_hw, lmstudio_hw)
    data["files"] = annotated["files"]
    if data.get("primary_file"):
        match = next((f for f in data["files"] if f["path"] == data["primary_file"]["path"]), None)
        if match:
            data["primary_file"] = match
    return data

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
    runtime_job_id: str | None = Field(default=None, max_length=200)

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


class CivitaiTokenInput(BaseModel):
    token: str = Field(min_length=1, max_length=512)

@app.get("/api/settings/civitai")
def civitai_token_status(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return {"configured": civitai_credentials.configured(db)}

@app.put("/api/settings/civitai")
def civitai_token_save(payload: CivitaiTokenInput, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            civitai_credentials.set_token(db, payload.token)
    return {"configured": True}

@app.delete("/api/settings/civitai")
def civitai_token_remove(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            civitai_credentials.delete_token(db)
    return {"configured": False}

@app.post("/api/settings/civitai/test")
def civitai_token_test(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        secret = civitai_credentials.get_token(db)
    if not secret:
        raise HTTPException(409, "No Civitai token configured")
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request("https://civitai.com/api/v1/models?limit=1",
            headers={"Authorization": "Bearer " + secret, "User-Agent": "ModelHarbor/1.0"})
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                raise HTTPException(502, "Civitai test failed")
    except urllib.error.HTTPError as exc:
        raise HTTPException(400, "Civitai rejected the request") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(503, "Civitai is unreachable") from exc
    return {"reachable": True, "note": "API reachable; download permissions may differ"}

@app.get("/api/downloads/next")
def next_download(agent: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            job = downloads.claim(db, agent)
            if job and job["provider"] == "civitai":
                job["civitai_token"] = civitai_credentials.get_token(db)
            return {"job": job}

@app.post("/api/downloads/{job_id}/progress")
def download_progress(job_id: str, payload: DownloadProgress, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return downloads.progress(db, job_id, payload.agent, payload.bytes_done, payload.runtime_job_id)

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

def _local_hashes(db, device, path):
    """(sha256, weights_sha256, size_bytes) of an inventory file."""
    row = db.execute("SELECT sha256,weights_sha256,size_bytes FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")
    if not row[0]:
        raise HTTPException(409, "This file has not been hashed yet; wait for the agent's next sync")
    return row

def _local_sha(db, device, path):
    return _local_hashes(db, device, path)[0]

@app.get("/api/civitai/check")
def civitai_check_file(device: str, path: str, q: str, x_modelharbor_token: str | None = Header(default=None)):
    """Search Civitai and say for every result whether it contains this exact file."""
    authorize(x_modelharbor_token)
    q = q.strip()
    if not 3 <= len(q) <= 120:
        raise HTTPException(422, "Search must contain 3–120 characters")
    with closing(connect()) as db:
        sha, weights, size = _local_hashes(db, device, path)
    try:
        return civitai_check(q, sha, weights, size, name=path.replace("\\", "/").rsplit("/", 1)[-1])
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Civitai is unavailable right now") from exc

@app.get("/api/civitai/models/{model_id}")
def civitai_model_versions(model_id: int, device: str, path: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        sha, weights, _ = _local_hashes(db, device, path)
    try:
        result = civitai_versions(model_id, sha, weights=weights)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Civitai is unavailable right now") from exc
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
            sha, weights, _ = _local_hashes(db, payload.device, payload.path)
            try:
                result = civitai_link(db, sha, payload.model_id, payload.version_id, payload.file_id, weights=weights)
            except (OSError, ValueError) as exc:
                raise HTTPException(502, "Civitai is unavailable right now") from exc
            if result:  # a linked file is no longer local only
                db.execute("DELETE FROM match_ignored WHERE sha256=?", (sha,))
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

class HuggingFaceLink(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)
    repository: str = Field(min_length=3, max_length=193)
    remote_path: str = Field(min_length=1, max_length=1024)

@app.post("/api/huggingface/link")
def link_hf_model(payload: HuggingFaceLink, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            result = link_huggingface(db, payload.device, payload.path, payload.repository, payload.remote_path)
            db.execute("DELETE FROM match_ignored WHERE sha256 IN (?, (SELECT sha256 FROM models WHERE device=? AND path=?))",
                       (ignore_key(payload.device, payload.path, None), payload.device, payload.path))
            return result

class ModelRef(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

@app.post("/api/huggingface/unlink")
def unlink_hf_model(payload: ModelRef, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            db.execute("DELETE FROM selected_matches WHERE device=? AND path=?", (payload.device, payload.path))
    return {"ok": True}

@app.get("/api/models/suggest-match")
def suggest_model_match(device: str, path: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return suggest_matches(db, device, path)

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

@app.get("/api/browse/huggingface")
def browse_huggingface(q: str = "", type: str = "", sort: str = "Trending",
                       x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    try:
        return browse.search_huggingface(q, type, sort)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Hugging Face is unavailable right now") from exc

@app.get("/api/browse/modelscope")
def browse_modelscope(q: str = "", type: str = "", sort: str = "Most Downloaded", cursor: str = "1",
                      x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    if not cursor.isdigit():
        raise HTTPException(422, "Invalid page cursor")
    try:
        return browse.search_modelscope(q, type, sort, int(cursor))
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "ModelScope is unavailable right now") from exc

@app.get("/api/modelscope/repository")
def modelscope_repository(repo: str, agent: str = "", x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        shas, hw = browse.local_shas(db), _hardware(db, agent)
    try:
        return fit.annotate_listing(browse.mark_huggingface(browse.list_modelscope_files(repo), shas), hw)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "ModelScope is unavailable right now") from exc

@app.get("/api/browse/civitai")
def browse_civitai(q: str = "", type: str = "", base: str = "", sort: str = "Most Downloaded", cursor: str = "",
                   agent: str = "", x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        shas, hw = browse.local_shas(db), _hardware(db, agent)
    try:
        return browse.search_civitai(q, type, base, sort, cursor, shas, hw=hw)
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Civitai is unavailable right now") from exc

class NewDownload(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    provider: str = Field(pattern=r"^(civitai|huggingface|modelscope|ollama|lemonade|lmstudio)$")
    model_id: int | None = Field(default=None, ge=1)
    version_id: int | None = Field(default=None, ge=1)
    file_id: int | None = Field(default=None, ge=1)
    repository: str | None = Field(default=None, max_length=193)
    remote_path: str | None = Field(default=None, max_length=1024)
    folder: str | None = Field(default=None, max_length=40)
    base_model: str | None = Field(default=None, max_length=100)
    trigger_words: list[str] | str | None = None

@app.post("/api/downloads/new", status_code=202)
def new_download(payload: NewDownload, x_modelharbor_token: str | None = Header(default=None)):
    """Download a model that is not on the PC. URL, file name and hash come from the provider, not the client."""
    authorize(x_modelharbor_token)
    extra = {}
    if payload.provider in ("ollama", "lemonade", "lmstudio"):  # a Hugging Face GGUF, pulled by the PC's LLM server
        repo = validate_repo(payload.repository or "")
        files = list_model_files(repo)["files"]
        if not any(f["path"] == payload.remote_path for f in files):
            raise HTTPException(404, "File not found in that repository")
        selected_name = PurePosixPath(payload.remote_path or "").name.casefold()
        same_name = [f for f in files if PurePosixPath(f["path"]).name.casefold() == selected_name]
        if len(same_name) > 1:
            if payload.provider in ("ollama", "lemonade"):
                runtime = "Ollama" if payload.provider == "ollama" else "Lemonade"
                raise HTTPException(422, f"{runtime}'s Hugging Face import identifies files by filename; this repository has duplicate filenames")
        with closing(connect()) as db:
            with db:
                if payload.provider == "lemonade":
                    size = next(f.get("size_bytes") for f in files if f["path"] == payload.remote_path)
                    return downloads.create_lemonade(db, payload.agent, repo, payload.remote_path, size)
                if payload.provider == "lmstudio":
                    return downloads.create_lmstudio(db, payload.agent, repo, payload.remote_path, files)
                return downloads.create_ollama(db, payload.agent, repo, payload.remote_path)
    if payload.provider == "civitai":
        try:
            model = _civitai_model(payload.model_id or 0, fetch)
        except (OSError, ValueError) as exc:
            raise HTTPException(502, "Civitai is unavailable right now") from exc
        version = next((v for v in model.get("modelVersions") or [] if v.get("id") == payload.version_id), None)
        file = version and next((f for f in version.get("files") or [] if f.get("id") == payload.file_id), None)
        if not file:
            raise HTTPException(404, "That file is not part of the chosen Civitai version")
        args = (f"https://civitai.com/api/download/models/{payload.version_id}?fileId={payload.file_id}",
                str(file.get("name") or ""), _file_sha(file), int(float(file.get("sizeKB") or 0) * 1024),
                browse.folder_for(model.get("type"), file.get("type"), version.get("baseModel")), f"civitai:{payload.file_id}")
    elif payload.provider == "modelscope":
        repo = validate_repo(payload.repository or "")
        try:
            listing = browse.list_modelscope_files(repo)
        except (OSError, ValueError) as exc:
            raise HTTPException(502, "ModelScope is unavailable right now") from exc
        item = next((f for f in listing["files"] if f["path"] == payload.remote_path), None)
        if not item:
            raise HTTPException(404, "File not found in that repository")
        args = (browse.modelscope_url(repo, item["path"]), item["name"], item["sha256"], item["size_bytes"],
                payload.folder or "loras", f"modelscope:{repo}/{item['path']}")
    else:
        repo = validate_repo(payload.repository or "")
        item = next((f for f in list_model_files(repo)["files"] if f["path"] == payload.remote_path), None)
        if not item:
            raise HTTPException(404, "File not found in that repository")
        args = ("https://huggingface.co/" + quote(repo, safe="/") + "/resolve/main/" + quote(item["path"], safe="/"),
                item["name"], item["sha256"], item["size_bytes"], payload.folder or "loras", f"huggingface:{repo}/{item['path']}")
        bm = payload.base_model or (payload.folder.partition("/")[2] if payload.folder and "/" in payload.folder else None)
        extra = {"base_model": bm, "trigger_words": payload.trigger_words}
    with closing(connect()) as db:
        with db:
            return downloads.create_new(db, payload.agent, *args, payload.provider, **extra)

class LMStudioActionRequest(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)
    action: str = Field(pattern=r"^(load|unload)$")
    instance_id: str | None = Field(default=None, max_length=512)
    context_length: int | None = Field(default=None, ge=1, le=1_000_000)

@app.post("/api/lmstudio/actions", status_code=202)
def lmstudio_action(payload: LMStudioActionRequest, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    if not payload.device.endswith(":lmstudio"):
        raise HTTPException(422, "Choose an LM Studio model")
    with closing(connect()) as db:
        with db:
            row = db.execute("SELECT kind,runtime_meta FROM models WHERE device=? AND path=?",
                             (payload.device, payload.path)).fetchone()
            if not row or not row[0].startswith("lmstudio/"):
                raise HTTPException(404, "LM Studio model not found in inventory")
            try:
                meta = json.loads(row[1]) if row[1] else {}
            except (TypeError, ValueError):
                meta = {}
            heartbeat = db.execute("SELECT last_seen,lmstudio FROM agent_heartbeats WHERE device=?", (payload.device.rsplit(":", 1)[0],)).fetchone()
            try:
                runtime_status = json.loads(heartbeat[1]).get("status") if heartbeat and heartbeat[1] else None
            except (TypeError, ValueError, AttributeError):
                runtime_status = None
            if runtime_status != "connected" or not heartbeat or int(time.time()) - heartbeat[0] > 3900:
                raise HTTPException(409, "LM Studio is not currently reachable from the PC's agent")
            model_key = meta.get("model_key")
            if not isinstance(model_key, str) or not model_key:
                raise HTTPException(409, "LM Studio model key is missing; refresh its inventory")
            instances = meta.get("loaded_instances") if isinstance(meta.get("loaded_instances"), list) else []
            if payload.action == "load":
                maximum = meta.get("max_context_length")
                if instances:
                    raise HTTPException(409, "This model is already loaded in LM Studio")
                if not payload.context_length or not isinstance(maximum, int) or payload.context_length > maximum:
                    raise HTTPException(422, "Choose a context length at or below LM Studio's reported model limit")
                instance_id = None
            else:
                if not payload.instance_id or not any(isinstance(x, dict) and x.get("id") == payload.instance_id for x in instances):
                    raise HTTPException(409, "That loaded model instance is no longer in the inventory; refresh first")
                instance_id = payload.instance_id
                payload.context_length = None
            return downloads.create_lmstudio_action(db, downloads.physical_device(payload.device), payload.device,
                payload.path, payload.action, model_key, instance_id, payload.context_length)

class ConvertRequest(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

class ConvertResult(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    error: str | None = Field(default=None, max_length=300)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    modified_ns: int | None = Field(default=None, ge=0)

@app.post("/api/converts", status_code=202)
def convert_model(payload: ConvertRequest, x_modelharbor_token: str | None = Header(default=None)):
    """Queue an int8_convrot conversion of a ComfyUI file on its PC. The original is left as it is."""
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return converts.request(db, payload.device, payload.path)

@app.get("/api/converts")
def list_converts(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return converts.recent(db)

@app.get("/api/converts/next")
def next_convert(agent: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return {"job": converts.claim(db, agent)}

@app.post("/api/converts/{job_id}/result")
def convert_result(job_id: str, payload: ConvertResult, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    result = payload.model_dump(include={"path", "sha256", "size_bytes", "modified_ns"})
    complete = all(v is not None for v in result.values())
    with closing(connect()) as db:
        with db:
            return converts.finish(db, job_id, payload.agent, payload.error, result if complete else None)

class DeleteRequest(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

class DeleteResult(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    error: str | None = Field(default=None, max_length=300)

@app.post("/api/models/delete", status_code=202)
def delete_model(payload: DeleteRequest, x_modelharbor_token: str | None = Header(default=None)):
    """Queue permanent deletion of a ComfyUI file (and its hard links) on its PC."""
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return deletes.request(db, payload.device, payload.path)

@app.get("/api/deletes/next")
def next_delete(agent: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return {"job": deletes.claim(db, agent)}

@app.get("/api/deletes/{job_id}")
def delete_status(job_id: str, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        return deletes.get(db, job_id)

@app.post("/api/deletes/{job_id}/result")
def delete_result(job_id: str, payload: DeleteResult, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            return deletes.finish(db, job_id, payload.agent, payload.error)

class MatchIgnore(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)
    ignored: bool

@app.post("/api/match-ignore")
def match_ignore(payload: MatchIgnore, x_modelharbor_token: str | None = Header(default=None)):
    """Mark a file as local only (not on any site) or put it back on the Unmatched list. Keyed by hash,
    or by device and path for LLM server models, which have none."""
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            if payload.device.endswith(":lemonade"):
                if not db.execute("SELECT 1 FROM models WHERE device=? AND path=?", (payload.device, payload.path)).fetchone():
                    raise HTTPException(404, "Model not found in inventory")
                sha = ignore_key(payload.device, payload.path, None)
            else:
                sha = _local_sha(db, payload.device, payload.path)
            if payload.ignored:
                db.execute("INSERT OR IGNORE INTO match_ignored VALUES (?,?)", (sha, int(time.time())))
            else:
                db.execute("DELETE FROM match_ignored WHERE sha256=?", (sha,))
    return {"ignored": payload.ignored}
