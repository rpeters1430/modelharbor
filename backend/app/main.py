"""ModelHarbor read-only API foundation."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    return db

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/models")
def models(x_modelharbor_token: str | None = Header(default=None)):
    authorize(x_modelharbor_token)
    with closing(connect()) as db:
        rows = db.execute("SELECT device,path,name,kind,size_bytes,modified_ns FROM models ORDER BY device,kind,name").fetchall()
    return [dict(zip(("device","path","name","kind","size_bytes","modified_ns"), row)) for row in rows]

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
            db.executemany("INSERT INTO models VALUES (?,?,?,?,?,?)",
                [(m.device,m.path,m.name,m.kind,m.size_bytes,m.modified_ns) for m in payload.models])
    return {"device": payload.device, "count": len(payload.models)}
