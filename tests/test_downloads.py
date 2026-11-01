"""Approved update downloads: NAS job planning and the agent's safe download."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from download_worker import download_file
from backend.app import downloads
from backend.app.hash_matches import ensure_tables as ensure_hash_tables
from backend.app.sources import ensure_tables as ensure_source_tables

OLD_SHA, NEW_SHA = "a" * 64, "b" * 64

@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT,
        size_bytes INTEGER, modified_ns INTEGER, file_id TEXT, sha256 TEXT, PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_source_tables(connection)
    downloads.ensure_tables(connection)
    connection.execute("INSERT INTO models VALUES ('pc:comfyui','C:/m/cool.safetensors','cool.safetensors','lora',5,1,NULL,?)", (OLD_SHA,))
    yield connection
    connection.close()

def civitai_update(db, file_name="cool_v2.safetensors"):
    db.execute("""INSERT INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,version_name,
        base_model,trained_words,published_at,nsfw,latest_version_id,latest_version_name,latest_file)
        VALUES (?,1,0,7,10,'Cool','v1.0','Pony','[]','2025',0,12,'v2.0',?)""",
        (OLD_SHA, json.dumps({"id": 120, "name": file_name, "sha256": NEW_SHA, "size_bytes": 5})))

def test_civitai_job_lifecycle(db):
    civitai_update(db)
    job = downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")
    assert downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")["id"] == job["id"]  # no duplicates
    claimed = downloads.claim(db, "pc")
    assert claimed["url"] == "https://civitai.com/api/download/models/12?fileId=120"
    assert (claimed["file_name"], claimed["expected_sha"]) == ("cool_v2.safetensors", NEW_SHA)
    assert downloads.claim(db, "pc") is None
    downloads.progress(db, job["id"], "pc", 3)
    assert downloads.finish(db, job["id"], "pc", NEW_SHA, None, "C:/m/cool_v2.safetensors")["state"] == "done"

def test_wrong_hash_report_fails_and_other_agents_cannot_report(db):
    civitai_update(db)
    job = downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")
    downloads.claim(db, "pc")
    with pytest.raises(HTTPException):
        downloads.finish(db, job["id"], "other-pc", NEW_SHA, None, "x")
    assert downloads.finish(db, job["id"], "pc", "c" * 64, None, "x")["state"] == "failed"

def test_no_update_or_unsafe_name_is_refused(db):
    with pytest.raises(HTTPException) as no_update:
        downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")
    assert no_update.value.status_code == 409
    civitai_update(db, "../../evil.safetensors")
    with pytest.raises(HTTPException) as unsafe:
        downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")
    assert unsafe.value.status_code == 422

def test_huggingface_copy_gets_hash_suffix(db):
    db.execute("""INSERT INTO selected_matches (device,path,repository,remote_path,sha256,local_size,
        local_modified_ns,selected_at,latest_sha) VALUES ('pc:comfyui','C:/m/cool.safetensors','o/r','sub/cool.safetensors',?,5,1,0,?)""",
        (OLD_SHA, NEW_SHA))
    downloads.create(db, "pc:comfyui", "C:/m/cool.safetensors")
    claimed = downloads.claim(db, "pc")
    assert claimed["url"] == "https://huggingface.co/o/r/resolve/main/sub/cool.safetensors"
    assert claimed["file_name"] == "cool.bbbbbbbb.safetensors"

class FakeResponse:
    def __init__(self, body, status=200, content_type="application/octet-stream"):
        self.body, self.status_code, self.headers = body, status, {"Content-Type": content_type}
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def iter_content(self, size):
        yield self.body

class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs["headers"]))
        return self.response

def agent_job(tmp_path, body, url="https://civitai.com/api/download/models/12?fileId=120", name="cool_v2.safetensors"):
    old = tmp_path / "cool.safetensors"
    old.write_bytes(b"old")
    return {"path": str(old), "url": url, "file_name": name, "size_bytes": len(body),
            "expected_sha": hashlib.sha256(body).hexdigest()}

def test_agent_downloads_next_to_old_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVITAI_API_TOKEN", "key")
    job = agent_job(tmp_path, b"new weights")
    session = FakeSession(FakeResponse(b"new weights"))
    sha, path = download_file(session, job, [str(tmp_path)], lambda done: None)
    assert Path(path) == tmp_path / "cool_v2.safetensors" and Path(path).read_bytes() == b"new weights"
    assert (tmp_path / "cool.safetensors").read_bytes() == b"old"  # old version kept
    assert session.calls[0][1] == {"Authorization": "Bearer key"}

def test_agent_rejects_bad_downloads_and_leaves_nothing(tmp_path):
    job = agent_job(tmp_path, b"expected")
    with pytest.raises(ValueError, match="hash did not match"):
        download_file(FakeSession(FakeResponse(b"tampered")), job, [str(tmp_path)], lambda d: None)
    with pytest.raises(ValueError, match="CIVITAI_API_TOKEN"):
        download_file(FakeSession(FakeResponse(b"<html>", content_type="text/html")), job, [str(tmp_path)], lambda d: None)
    with pytest.raises(ValueError, match="host is not allowed"):
        download_file(FakeSession(FakeResponse(b"x")), {**job, "url": "https://evil.example/x"}, [str(tmp_path)], lambda d: None)
    with pytest.raises(ValueError, match="Unsafe"):
        download_file(FakeSession(FakeResponse(b"x")), {**job, "file_name": "..\\x.safetensors"}, [str(tmp_path)], lambda d: None)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cool.safetensors"]
    (tmp_path / "cool_v2.safetensors").write_bytes(b"mine")
    with pytest.raises(ValueError, match="already exists"):
        download_file(FakeSession(FakeResponse(b"expected")), job, [str(tmp_path)], lambda d: None)
    assert (tmp_path / "cool_v2.safetensors").read_bytes() == b"mine"
