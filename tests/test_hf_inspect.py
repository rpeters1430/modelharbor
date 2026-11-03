"""Tests for Hugging Face URL parsing, base model detection, inspection, and update tracking."""
import json
import sqlite3
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.hf_inspect import (
    parse_hf_url, detect_base_model, detect_model_kind, score_file, inspect_huggingface
)
from backend.app.main import app, connect, TOKEN
from backend.app import downloads
from backend.app.hash_matches import ensure_tables as ensure_hash_tables
from backend.app.sources import ensure_tables as ensure_source_tables, annotate

def test_parse_hf_url():
    repo, path = parse_hf_url("https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora")
    assert (repo, path) == ("axiomofmind/Moss-image-nsfw-uncensored-lora", None)

    repo, path = parse_hf_url("https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora/")
    assert (repo, path) == ("axiomofmind/Moss-image-nsfw-uncensored-lora", None)

    repo, path = parse_hf_url("https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora/blob/main/MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors")
    assert (repo, path) == ("axiomofmind/Moss-image-nsfw-uncensored-lora", "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors")

    repo, path = parse_hf_url("https://hf.co/owner/repo/resolve/main/sub/model.safetensors")
    assert (repo, path) == ("owner/repo", "sub/model.safetensors")

    repo, path = parse_hf_url("owner/repo")
    assert (repo, path) == ("owner/repo", None)

    for invalid in ("", "not-a-url", "https://other.com/a/b", "onlyonepart"):
        with pytest.raises(HTTPException):
            parse_hf_url(invalid)

def test_detect_base_model():
    fam, label = detect_base_model({"base_model": "OpenMOSS-Team/MOSS-Image-SFT"}, ["lora"], "repo/test", [])
    assert (fam, label) == ("MOSS", "MOSS")

    fam, label = detect_base_model({}, ["diffusers:FluxPipeline", "flux"], "black-forest-labs/FLUX.1-dev", [])
    assert (fam, label) == ("Flux", "Flux.1")

    fam, label = detect_base_model({"base_model": ["stabilityai/stable-diffusion-xl-base-1.0"]}, [], "user/lora", [])
    assert (fam, label) == ("SDXL", "SDXL 1.0")

    fam, label = detect_base_model({}, [], "user/my-pony-v7-lora", [])
    assert (fam, label) == ("PonyV7", "Pony V7")

    fam, label = detect_base_model({}, ["wan-video"], "Wan-AI/Wan2.1-T2V-14B", [])
    assert (fam, label) == ("Wan", "Wan Video")

def test_detect_model_kind():
    kind, folder = detect_model_kind({"tags": ["lora"]}, ["text-to-image"], "user/model", [])
    assert (kind, folder) == ("LORA", "loras")

    kind, folder = detect_model_kind({}, [], "user/cool-lora", [])
    assert (kind, folder) == ("LORA", "loras")

    kind, folder = detect_model_kind({}, ["vae"], "user/vae-model", [])
    assert (kind, folder) == ("VAE", "vae")

    kind, folder = detect_model_kind({}, ["text-to-image"], "user/checkpoint-model", [])
    assert (kind, folder) == ("Checkpoint", "checkpoints")

def test_score_file():
    comfy_file = {"path": "model-ComfyUI.safetensors", "name": "model-ComfyUI.safetensors", "sha256": "a"*64, "size_bytes": 100_000_000}
    pytorch_file = {"path": "pytorch_lora_weights.safetensors", "name": "pytorch_lora_weights.safetensors", "sha256": "b"*64, "size_bytes": 100_000_000}
    nested_file = {"path": "checkpoint-100/model-ComfyUI.safetensors", "name": "model-ComfyUI.safetensors", "sha256": "c"*64, "size_bytes": 100_000_000}

    assert score_file(comfy_file, is_lora=True, is_comfy=False) > score_file(pytorch_file, is_lora=True, is_comfy=False)
    assert score_file(comfy_file, is_lora=True, is_comfy=False) > score_file(nested_file, is_lora=True, is_comfy=False)

def test_inspect_huggingface_repo():
    meta = {
        "id": "axiomofmind/Moss-image-nsfw-uncensored-lora",
        "tags": ["diffusers", "safetensors", "text-to-image", "lora", "base_model:OpenMOSS-Team/MOSS-Image-SFT"],
        "cardData": {
            "base_model": "OpenMOSS-Team/MOSS-Image-SFT",
            "instance_prompt": "moss_xvstyle",
            "tags": ["lora"]
        },
        "downloads": 120,
        "likes": 45,
        "createdAt": "2025-01-01T00:00:00.000Z"
    }
    listing_files = [
        {"path": "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors", "name": "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
         "size_bytes": 74379592, "sha256": "1"*64, "url": "https://huggingface.co/..."},
        {"path": "pytorch_lora_weights.safetensors", "name": "pytorch_lora_weights.safetensors",
         "size_bytes": 74377208, "sha256": "2"*64, "url": "https://huggingface.co/..."},
        {"path": "README.md", "name": "README.md", "size_bytes": 500, "sha256": None, "url": "..."}
    ]

    with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(meta).encode()):
        with patch("backend.app.hf_inspect.list_model_files", return_value={"files": listing_files}):
            data = inspect_huggingface("https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora")

    assert data["repository"] == "axiomofmind/Moss-image-nsfw-uncensored-lora"
    assert data["base_model"] == "MOSS"
    assert data["family"] == "MOSS"
    assert data["model_type"] == "LORA"
    assert data["target_folder"] == "loras/MOSS"
    assert data["trigger_words"] == ["moss_xvstyle"]
    assert data["primary_file"]["name"] == "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors"
    assert len(data["files"]) == 3

def test_download_hf_model_matches_and_tracks_updates(tmp_path, monkeypatch):
    from backend.app import main
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(main, "DB", db_path)
    monkeypatch.setattr(main, "TOKEN", "test-token")

    client = TestClient(app)
    headers = {"X-ModelHarbor-Token": "test-token"}

    db = connect()
    SHA = "e" * 64

    # Simulate creating a new download job for a Hugging Face model
    job = downloads.create_new(
        db, agent="my-pc",
        url="https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora/resolve/main/MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
        file_name="MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
        expected_sha=SHA,
        size_bytes=74379592,
        folder="loras/MOSS",
        source_ref="huggingface:axiomofmind/Moss-image-nsfw-uncensored-lora/MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
        provider="huggingface",
        base_model="MOSS",
        trigger_words=["moss_xvstyle"]
    )

    # Agent claims it
    claimed = downloads.claim(db, "my-pc")
    assert claimed["id"] == job["id"]

    # Agent finishes it
    result_path = "C:/ComfyUI/models/loras/MOSS/MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors"
    downloads.finish(db, job["id"], "my-pc", SHA, None, result_path)
    db.commit()

    # Check that selected_matches was automatically populated!
    row = db.execute("SELECT repository, remote_path, sha256, base_model, trigger_words FROM selected_matches WHERE path=?", (result_path,)).fetchone()
    assert row is not None
    assert row[0] == "axiomofmind/Moss-image-nsfw-uncensored-lora"
    assert row[1] == "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors"
    assert row[2] == SHA
    assert row[3] == "MOSS"
    assert json.loads(row[4]) == ["moss_xvstyle"]

    # When inventory is scanned:
    inventory_models = [{
        "device": "my-pc:comfyui",
        "path": result_path,
        "name": "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
        "kind": "lora",
        "size_bytes": 74379592,
        "modified_ns": 1234567,
        "sha256": SHA,
    }]
    client.post("/api/inventory", json={"device": "my-pc:comfyui", "models": inventory_models}, headers=headers)

    # Verify annotate() attaches the Hugging Face source, base model, and trigger words!
    records = db.execute("SELECT device,path,name,kind,size_bytes,modified_ns,sha256 FROM models").fetchall()
    annotated = annotate(db, [dict(zip(("device","path","name","kind","size_bytes","modified_ns","sha256"), r)) for r in records])
    assert len(annotated) == 1
    src = annotated[0]["source"]
    assert src["provider"] == "huggingface"
    assert src["model_name"] == "axiomofmind/Moss-image-nsfw-uncensored-lora"
    assert src["base_model"] == "MOSS"
    assert src["trained_words"] == ["moss_xvstyle"]
    assert "blob/main/MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors" in src["url"]

    # Verify update check: if latest_sha changes on main, an update is flagged!
    NEW_SHA = "f" * 64
    db.execute("UPDATE selected_matches SET latest_sha=? WHERE path=?", (NEW_SHA, result_path))
    annotated_update = annotate(db, [dict(zip(("device","path","name","kind","size_bytes","modified_ns","sha256"), r)) for r in records])
    assert annotated_update[0]["update"] == {
        "provider": "huggingface",
        "version_name": "File changed on main",
        "url": src["url"]
    }

def test_inspect_api_endpoint(tmp_path, monkeypatch):
    from backend.app import main
    db_path = tmp_path / "test_endpoint.db"
    monkeypatch.setattr(main, "DB", db_path)
    monkeypatch.setattr(main, "TOKEN", "test-token")

    client = TestClient(app)
    headers = {"X-ModelHarbor-Token": "test-token"}

    mock_meta = {
        "id": "axiomofmind/Moss-image-nsfw-uncensored-lora",
        "tags": ["diffusers", "safetensors", "text-to-image", "lora", "base_model:OpenMOSS-Team/MOSS-Image-SFT"],
        "cardData": {
            "base_model": "OpenMOSS-Team/MOSS-Image-SFT",
            "instance_prompt": "moss_xvstyle",
            "tags": ["lora"]
        },
        "downloads": 150,
        "likes": 50,
        "createdAt": "2025-01-01T00:00:00.000Z"
    }
    mock_files = [
        {"path": "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors", "name": "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
         "size_bytes": 74379592, "sha256": "a"*64, "url": "https://huggingface.co/..."},
        {"path": "pytorch_lora_weights.safetensors", "name": "pytorch_lora_weights.safetensors",
         "size_bytes": 74377208, "sha256": "b"*64, "url": "https://huggingface.co/..."}
    ]

    with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(mock_meta).encode()):
        with patch("backend.app.hf_inspect.list_model_files", return_value={"files": mock_files}):
            res = client.get("/api/huggingface/inspect", params={"url": "https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora"}, headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["repository"] == "axiomofmind/Moss-image-nsfw-uncensored-lora"
            assert data["base_model"] == "MOSS"
            assert data["family"] == "MOSS"
            assert data["model_type"] == "LORA"
            assert data["target_folder"] == "loras/MOSS"
            assert data["trigger_words"] == ["moss_xvstyle"]
            assert data["primary_file"]["name"] == "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors"
            assert len(data["files"]) == 2

            # Now test new_download endpoint with the inspected model
            dl_res = client.post("/api/downloads/new", json={
                "agent": "my-pc",
                "provider": "huggingface",
                "repository": data["repository"],
                "remote_path": data["primary_file"]["path"],
                "folder": data["target_folder"],
                "base_model": data["base_model"],
                "trigger_words": data["trigger_words"]
            }, headers=headers)
            assert dl_res.status_code == 202
            job = dl_res.json()
            assert job["state"] == "queued"
            assert job["target_folder"] == "loras/MOSS"
