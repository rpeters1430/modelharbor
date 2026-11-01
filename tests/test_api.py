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

def test_agent_status_and_scan_request(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "status.sqlite3")
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "test-token"}
    assert client.post("/api/agents/pc/scan", headers=headers).status_code == 404  # unknown agent
    client.post("/api/agents/heartbeat", headers=headers, json={"device": "pc", "comfyui_count": 1, "ollama_count": 0})
    progress = {"device": "pc", "phase": "hashing", "done": 3, "total": 10, "done_bytes": 30, "total_bytes": 100, "current": "C:/m/a.safetensors"}
    assert client.post("/api/agents/status", headers=headers, json=progress).status_code == 200
    assert client.post("/api/agents/status", headers=headers, json={**progress, "phase": "deleting"}).status_code == 422
    [agent] = client.get("/api/agents", headers=headers).json()
    assert agent["status"]["phase"] == "hashing" and agent["status"]["done"] == 3
    assert agent["worker_running"] is False and agent["scan_requested"] is False
    assert client.post("/api/agents/pc/scan", headers=headers).status_code == 202
    assert client.get("/api/agents", headers=headers).json()[0]["scan_requested"] is True
    assert client.get("/api/agents/pc/scan-request", headers=headers).json() == {"scan": True}
    assert client.get("/api/agents/pc/scan-request", headers=headers).json() == {"scan": False}  # taken once
    [agent] = client.get("/api/agents", headers=headers).json()
    assert agent["worker_running"] is True and agent["scan_requested"] is False

def test_new_download_endpoint_builds_job_from_provider_data(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "new.sqlite3")
    model = {"type": "LORA", "modelVersions": [{"id": 12, "files": [
        {"id": 120, "type": "Model", "name": "cool.safetensors", "sizeKB": 2.0, "hashes": {"SHA256": "B" * 64}}]}]}
    monkeypatch.setattr(main, "_civitai_model", lambda model_id, get: model)
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "test-token"}
    body = {"agent": "pc", "provider": "civitai", "model_id": 1, "version_id": 12, "file_id": 120}
    job = client.post("/api/downloads/new", headers=headers, json=body).json()
    assert (job["file_name"], job["target_folder"], job["source_ref"]) == ("cool.safetensors", "loras", "civitai:120")
    assert client.post("/api/downloads/new", headers=headers, json={**body, "file_id": 999}).status_code == 404
