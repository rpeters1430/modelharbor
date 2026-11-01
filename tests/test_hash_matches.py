"""Offline coverage for hash requests, mismatch protection and explicit selection."""
import sqlite3
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from backend.app.hash_matches import (
    ensure_tables, queue_job, claim_job, finish_job, get_job, select_match, selected_for_inventory
)

DIGEST="a"*64
OTHER="b"*64

@pytest.fixture
def db():
    connection=sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models
      (device TEXT, path TEXT, size_bytes INTEGER, modified_ns INTEGER, kind TEXT,
       PRIMARY KEY(device,path))""")
    connection.execute("INSERT INTO models VALUES (?,?,?,?,?)",
                       ("pc:comfyui","C:/models/m.safetensors",145,1234,"lora"))
    ensure_tables(connection)
    yield connection
    connection.close()

def remote():
    return {"sha256": DIGEST,"size_bytes":145}

def queue(db):
    with patch("backend.app.hash_matches._remote_file",return_value=remote()):
        return queue_job(db,"pc:comfyui","C:/models/m.safetensors","owner/collection","m.safetensors")

def test_verified_then_explicitly_selected(db):
    job=queue(db)
    assert job["state"]=="queued"
    assert selected_for_inventory(db)==[]
    claimed=claim_job(db,"pc:comfyui")
    assert claimed["path"]=="C:/models/m.safetensors"
    assert finish_job(db,job["id"],"pc:comfyui",DIGEST,None)["state"]=="verified"
    assert selected_for_inventory(db)==[]
    with patch("backend.app.hash_matches._remote_file",return_value=remote()):
        selection=select_match(db,job["id"])
    assert selection["selected"] is True
    assert selected_for_inventory(db)[0]["remote_path"]=="m.safetensors"

def test_mismatch_cannot_be_selected(db):
    job=queue(db)
    claim_job(db,"pc:comfyui")
    assert finish_job(db,job["id"],"pc:comfyui",OTHER,None)["state"]=="mismatch"
    with pytest.raises(HTTPException) as exc:
        select_match(db,job["id"])
    assert exc.value.status_code==409

def test_stale_inventory_cannot_confirm(db):
    job=queue(db)
    claim_job(db,"pc:comfyui")
    db.execute("UPDATE models SET modified_ns=9876")
    assert finish_job(db,job["id"],"pc:comfyui",DIGEST,None)["state"]=="stale"

def test_remote_replaced_after_verification_rejected(db):
    job=queue(db)
    claim_job(db,"pc:comfyui")
    finish_job(db,job["id"],"pc:comfyui",DIGEST,None)
    with patch("backend.app.hash_matches._remote_file",return_value={"sha256":OTHER,"size_bytes":145}):
        with pytest.raises(HTTPException):
            select_match(db,job["id"])

def test_queue_does_not_accept_ollama_devices(db):
    with patch("backend.app.hash_matches._remote_file",return_value=remote()):
        with pytest.raises(HTTPException):
            queue_job(db,"pc:ollama","C:/models/m.safetensors","owner/collection","m.safetensors")

def test_duplicate_active_job_reused(db):
    first=queue(db)
    second=queue(db)
    assert first["id"]==second["id"]

def test_invalid_digest_rejected(db):
    job=queue(db)
    claim_job(db,"pc:comfyui")
    with pytest.raises(HTTPException):
        finish_job(db,job["id"],"pc:comfyui","xyz",None)

def test_selected_association_disappears_when_inventory_changes(db):
    job=queue(db)
    claim_job(db,"pc:comfyui")
    finish_job(db,job["id"],"pc:comfyui",DIGEST,None)
    with patch("backend.app.hash_matches._remote_file",return_value=remote()):
        select_match(db,job["id"])
    assert len(selected_for_inventory(db))==1
    db.execute("UPDATE models SET modified_ns=9876")
    assert selected_for_inventory(db)==[]

def test_inventory_hash_resolves_without_worker(db):
    with patch("backend.app.hash_matches._remote_file",return_value=remote()):
        good=queue_job(db,"pc:comfyui","C:/models/m.safetensors","owner/collection","m.safetensors",DIGEST)
        bad=queue_job(db,"pc:comfyui","C:/models/m.safetensors","owner/collection","other.safetensors",OTHER)
    assert (good["state"],bad["state"])==("verified","mismatch")
    assert claim_job(db,"pc:comfyui") is None
