import os
import time
from contextlib import closing

os.environ["MODELHARBOR_AGENT_TOKEN"] = "test-token"
from fastapi.testclient import TestClient
from backend.app import main


def setup_device(tmp_path, monkeypatch, device="old%_pc"):
    monkeypatch.setattr(main, "DB", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(main, "TOKEN", "test-token")
    client = TestClient(main.app)
    headers = {"X-ModelHarbor-Token": "test-token"}
    for name in (device, device + "-other"):
        assert client.post("/api/agents/heartbeat", headers=headers,
                           json={"device": name, "comfyui_count": 1}).status_code == 200
    with closing(main.connect()) as db, db:
        db.execute("UPDATE agent_heartbeats SET last_seen=1 WHERE device=?", (device,))
        db.execute("INSERT INTO agent_status(device,phase,worker_seen,scan_requested_at) VALUES (?,'scanning',1,1)", (device,))
        for source in (device, *(device + ":" + r for r in ("comfyui", "ollama", "lemonade", "lmstudio")), device + "-other:comfyui"):
            db.execute("INSERT INTO models(device,path,name,kind,size_bytes,modified_ns) VALUES (?,'model','model','lora',1,1)", (source,))
            db.execute("INSERT INTO selected_matches(device,path,repository,remote_path,sha256,local_size,local_modified_ns,selected_at) VALUES (?,'model','a/b','model',?,1,1,1)", (source, "a" * 64))
            db.execute("INSERT INTO hash_jobs(id,device,path,repository,remote_path,remote_sha,remote_size,local_size,local_modified_ns,state,created_at) VALUES (?,?,'model','a/b','model',?,1,1,1,'queued',1)", (source, source, "a" * 64))
        for agent in (device, device + "-other"):
            source = agent + ":comfyui"
            db.execute("INSERT INTO download_jobs(id,agent,device,path,provider,file_name,state,created_at,updated_at) VALUES (?,?,?,'model','huggingface','model','queued',1,1)", (agent, agent, source))
            db.execute("INSERT INTO delete_jobs(id,agent,device,paths,sha256,state,created_at,updated_at) VALUES (?,?,?,'[]',?,'queued',1,1)", (agent, agent, source, "a" * 64))
            db.execute("INSERT INTO convert_jobs(id,agent,device,path,sha256,state,created_at,updated_at) VALUES (?,?,?,'model',?,'queued',1,1)", (agent, agent, source, "a" * 64))
    return client, headers, device


def test_remove_offline_device_cleans_inventory_and_jobs(tmp_path, monkeypatch):
    client, headers, device = setup_device(tmp_path, monkeypatch)
    response = client.delete("/api/agents/" + device, headers=headers)
    assert response.status_code == 200
    assert response.json()["models_removed"] == 5
    with closing(main.connect()) as db:
        for table in ("models", "selected_matches", "hash_jobs", "download_jobs", "delete_jobs", "convert_jobs", "agent_heartbeats"):
            remaining = [r[0] for r in db.execute(f"SELECT device FROM {table}")]
            assert remaining == [device + "-other" + ("" if table == "agent_heartbeats" else ":comfyui")]
        assert db.execute("SELECT COUNT(*) FROM agent_status").fetchone()[0] == 0
    assert client.delete("/api/agents/" + device, headers=headers).status_code == 404
    assert client.post("/api/agents/heartbeat", headers=headers, json={"device": device, "comfyui_count": 0}).status_code == 200
    assert len(client.get("/api/agents", headers=headers).json()) == 2


def test_remove_device_requires_auth_and_rejects_live_agent(tmp_path, monkeypatch):
    client, headers, device = setup_device(tmp_path, monkeypatch)
    assert client.delete("/api/agents/" + device).status_code == 401
    assert client.delete("/api/agents/" + device + "-other", headers=headers).status_code == 409
    with closing(main.connect()) as db, db:
        db.execute("UPDATE agent_status SET worker_seen=? WHERE device=?", (int(time.time()), device))
    assert client.delete("/api/agents/" + device, headers=headers).status_code == 409
    with closing(main.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 6
