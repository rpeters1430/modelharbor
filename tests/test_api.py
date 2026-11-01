import os
os.environ["MODELHARBOR_AGENT_TOKEN"] = "test-token"
from fastapi.testclient import TestClient
from backend.app.main import app

def test_health():
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}

def test_auth_required():
    assert TestClient(app).get("/api/models").status_code == 401

def test_inventory_upgrades_old_database(tmp_path, monkeypatch):
    import sqlite3
    from backend.app import main
    db_path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(db_path)
    old.execute("""CREATE TABLE models (device TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
        kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL, PRIMARY KEY (device, path))""")
    old.execute("""CREATE TABLE selected_matches (device TEXT NOT NULL, path TEXT NOT NULL, repository TEXT NOT NULL,
        remote_path TEXT NOT NULL, sha256 TEXT NOT NULL, local_size INTEGER NOT NULL,
        local_modified_ns INTEGER NOT NULL, selected_at INTEGER NOT NULL, PRIMARY KEY(device,path))""")
    old.commit(); old.close()
    monkeypatch.setattr(main, "DB", db_path)
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "test-token"}
    model = {"device": "pc:comfyui", "path": "C:/m/a.safetensors", "name": "a.safetensors", "kind": "lora",
             "size_bytes": 5, "modified_ns": 1, "file_id": "1:2", "sha256": "a" * 64}
    assert client.post("/api/inventory", json={"device": "pc:comfyui", "models": [model]}, headers=headers).status_code == 200
    bad = {**model, "sha256": "not-a-hash"}
    assert client.post("/api/inventory", json={"device": "pc:comfyui", "models": [bad]}, headers=headers).status_code == 422
    [row] = client.get("/api/models", headers=headers).json()
    assert row["sha256"] == "a" * 64 and row["source"] is None and row["update"] is None

def test_dashboard_files_are_revalidated():
    client = TestClient(app)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
