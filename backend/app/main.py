"""ModelHarbor read-only API foundation."""
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .intelligence import enrich

DB = Path(os.environ.get("MODELHARBOR_DB", "/data/modelharbor.sqlite3"))
TOKEN = os.environ.get("MODELHARBOR_AGENT_TOKEN", "")
app = FastAPI(title="ModelHarbor", version="0.1.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

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

class Heartbeat(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    comfyui_count: int = Field(ge=0)
    ollama_count: int | None = Field(default=None, ge=0)

class Inventory(BaseModel):
    device: str
    models: list[ModelRecord]

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS models (
        device TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
        kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
        PRIMARY KEY (device, path))""")
    if "file_id" not in [row[1] for row in db.execute("PRAGMA table_info(models)")]:
        db.execute("ALTER TABLE models ADD COLUMN file_id TEXT")
    return db

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/models")
def models(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        rows = db.execute("SELECT device,path,name,kind,size_bytes,modified_ns,file_id FROM models ORDER BY device,kind,name").fetchall()
    return enrich([dict(zip(("device","path","name","kind","size_bytes","modified_ns","file_id"), row)) for row in rows])

def authorize(value):
    if not TOKEN or value != TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/api/inventory")
def inventory(payload: Inventory, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    if len(payload.models) > 100000 or any(m.device != payload.device for m in payload.models):
        raise HTTPException(status_code=422, detail="Invalid inventory")
    with closing(connect()) as db:
        with db:
            db.execute("DELETE FROM models WHERE device=?", (payload.device,))
            db.executemany("INSERT INTO models (device,path,name,kind,size_bytes,modified_ns,file_id) VALUES (?,?,?,?,?,?,?)",
                [(m.device,m.path,m.name,m.kind,m.size_bytes,m.modified_ns,m.file_id) for m in payload.models])
    return {"device": payload.device, "count": len(payload.models)}

@app.post("/api/agents/heartbeat")
def heartbeat(payload: Heartbeat, x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_heartbeats
                (device TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,
                 comfyui_count INTEGER NOT NULL, ollama_count INTEGER)""")
            db.execute("""INSERT INTO agent_heartbeats VALUES (?,?,?,?)
                ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen,
                comfyui_count=excluded.comfyui_count,ollama_count=excluded.ollama_count""",
                (payload.device, int(time.time()), payload.comfyui_count, payload.ollama_count))
    return {"ok": True}

@app.get("/api/agents")
def agents(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS agent_heartbeats
            (device TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,
             comfyui_count INTEGER NOT NULL, ollama_count INTEGER)""")
        rows = db.execute("SELECT device,last_seen,comfyui_count,ollama_count FROM agent_heartbeats ORDER BY device").fetchall()
    now = int(time.time())
    return [{"device": r[0], "last_seen": r[1], "online": now-r[1] <= 3900,
             "comfyui_count":r[2], "ollama_count":r[3]} for r in rows]
