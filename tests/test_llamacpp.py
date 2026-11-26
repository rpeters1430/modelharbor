"""llama.cpp downloads: queue a hash-checked GGUF, save it into llama-server's models folder, restart it."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import download_worker
import llamacpp_scan
from backend.app import downloads
from tests.test_api import app  # sets the test token before main is imported
from tests.test_downloads import FakeResponse, FakeSession, db  # noqa: F401  (db is a fixture)

BODY = b"gguf weights"
SHA = hashlib.sha256(BODY).hexdigest()
REPO_FILE = {"path": "sub/gpt-oss-20b-MXFP4.gguf", "size_bytes": len(BODY), "sha256": SHA.upper()}


def test_llamacpp_job_names_the_model_by_file_and_is_deduplicated(db):
    job = downloads.create_llamacpp(db, "pc", "ggml-org/gpt-oss-20b-GGUF", REPO_FILE)
    assert downloads.create_llamacpp(db, "pc", "ggml-org/gpt-oss-20b-GGUF", REPO_FILE)["id"] == job["id"]
    assert (job["provider"], job["device"], job["file_name"], job["source_ref"]) == (
        "llamacpp", "pc:llamacpp", "gpt-oss-20b-MXFP4.gguf", "llamacpp:gpt-oss-20b-MXFP4.gguf")
    claimed = downloads.claim(db, "pc")
    assert claimed["url"] == "https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/sub/gpt-oss-20b-MXFP4.gguf"
    assert claimed["expected_sha"] == SHA  # lower-cased, so the agent's digest compares equal
    assert downloads.finish(db, job["id"], "pc", "f" * 64, None, "/m/x.gguf")["state"] == "failed"


def test_llamacpp_refuses_files_it_cannot_serve(db):
    for bad in ({**REPO_FILE, "path": "mmproj-model-f16.gguf"}, {**REPO_FILE, "path": "m-00001-of-00002.gguf"},
                {**REPO_FILE, "path": "model.safetensors"}, {**REPO_FILE, "sha256": None}):
        with pytest.raises(HTTPException):
            downloads.create_llamacpp(db, "pc", "o/r", bad)


def test_new_llamacpp_download_through_the_api(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "llamacpp.sqlite3")
    monkeypatch.setattr(main, "list_model_files", lambda repo: {"files": [
        REPO_FILE, {"path": "a/dup.gguf", "size_bytes": 1, "sha256": SHA}, {"path": "b/dup.gguf", "size_bytes": 1, "sha256": SHA}]})
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "test-token"}
    body = {"agent": "pc", "provider": "llamacpp", "repository": "ggml-org/gpt-oss-20b-GGUF"}
    job = client.post("/api/downloads/new", headers=headers, json={**body, "remote_path": REPO_FILE["path"]}).json()
    assert (job["provider"], job["device"], job["size_bytes"]) == ("llamacpp", "pc:llamacpp", len(BODY))
    assert client.post("/api/downloads/new", headers=headers, json={**body, "remote_path": "a/dup.gguf"}).status_code == 422
    # The heartbeat's llama.cpp count tells the dashboard to offer llama.cpp for this PC.
    beat = {"device": "pc", "comfyui_count": 0, "llamacpp_count": 2}
    assert client.post("/api/agents/heartbeat", headers=headers, json=beat).status_code == 200
    [agent] = client.get("/api/agents", headers=headers).json()
    assert agent["llamacpp_count"] == 2


def test_agent_saves_into_the_models_folder_and_restarts_llama_server(tmp_path, monkeypatch):
    restarts = []
    monkeypatch.setattr(llamacpp_scan, "restart", lambda command: restarts.append(command))
    job = {"url": "https://huggingface.co/o/r/resolve/main/m.gguf", "file_name": "m.gguf",
           "size_bytes": len(BODY), "expected_sha": SHA}
    sha, path = download_worker.download_llamacpp(FakeSession(FakeResponse(BODY)), job, str(tmp_path),
                                                  ["docker", "restart", "llama-cpp"], lambda done: None)
    assert (sha, Path(path)) == (SHA, tmp_path / "m.gguf") and Path(path).read_bytes() == BODY
    assert restarts == [["docker", "restart", "llama-cpp"]]
    with pytest.raises(ValueError, match="already exists"):  # never overwrites
        download_worker.download_llamacpp(FakeSession(FakeResponse(BODY)), job, str(tmp_path), [], lambda d: None)
    with pytest.raises(ValueError, match="llamacpp_models_dir"):
        download_worker.download_llamacpp(FakeSession(FakeResponse(BODY)), job, None, [], lambda d: None)
    assert len(restarts) == 1


def test_scan_lists_gguf_files_in_the_folder_only(tmp_path):
    (tmp_path / "a.gguf").write_bytes(b"1")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.gguf").write_bytes(b"2")  # llama-server ignores subfolders too
    [model] = llamacpp_scan.scan(tmp_path, "pc:llamacpp")
    assert (model["name"], model["kind"], model["device"], model["size_bytes"]) == ("a.gguf", "llamacpp/gguf", "pc:llamacpp", 1)


def test_restart_reports_failures_and_can_be_switched_off():
    assert llamacpp_scan.restart([]) is None
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    assert llamacpp_scan.restart(["docker", "restart", "llama-cpp"], run=ok) is None
    bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "No such container: llama-cpp")
    assert "No such container" in llamacpp_scan.restart(["docker", "restart", "llama-cpp"], run=bad)


class DeleteSession:
    def __init__(self, job):
        self.job, self.results = job, []
    def get(self, url, **kwargs):
        job = self.job
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"job": job}})()
    def post(self, url, json=None, **kwargs):
        self.results.append(json)
        return type("R", (), {"raise_for_status": lambda self: None})()


def test_deleting_a_llamacpp_model_restarts_llama_server(tmp_path, monkeypatch):
    restarts = []
    monkeypatch.setattr(llamacpp_scan, "restart", lambda command: restarts.append(command))
    llama, comfy = tmp_path / "llama", tmp_path / "comfy"
    llama.mkdir(), comfy.mkdir()
    (llama / "m.gguf").write_bytes(BODY)
    session = DeleteSession({"id": "1", "paths": [str(llama / "m.gguf")], "sha256": SHA})
    assert download_worker.process_delete(session, "http://nas", "t", "pc", [str(comfy)], str(llama), ["restart"])
    assert session.results == [{"agent": "pc", "error": None}] and not (llama / "m.gguf").exists()
    assert restarts == [["restart"]]
    # A ComfyUI delete leaves llama-server alone.
    (comfy / "x.safetensors").write_bytes(BODY)
    session = DeleteSession({"id": "2", "paths": [str(comfy / "x.safetensors")], "sha256": SHA})
    download_worker.process_delete(session, "http://nas", "t", "pc", [str(comfy)], str(llama), ["restart"])
    assert restarts == [["restart"]]


def test_delete_requests_accept_llamacpp_files(db):
    from backend.app import deletes
    deletes.ensure_tables(db)
    db.execute("INSERT INTO models VALUES ('pc:llamacpp','/m/a.gguf','a.gguf','llamacpp/gguf',5,1,NULL,?)", (SHA,))
    job = deletes.request(db, "pc:llamacpp", "/m/a.gguf")
    assert (job["agent"], job["paths"]) == ("pc", ["/m/a.gguf"])
    db.execute("INSERT INTO models VALUES ('pc:ollama','o://x','x','ollama/x',5,1,NULL,?)", (SHA,))
    with pytest.raises(HTTPException):
        deletes.request(db, "pc:ollama", "o://x")
