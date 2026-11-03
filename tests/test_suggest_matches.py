"""Tests for unmatched model auto-suggestions and 1-click Hugging Face linking."""
import json
import sqlite3
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.app.suggest_matches import clean_search_query, suggest_matches, link_huggingface
from backend.app.main import app, connect
from backend.app.hash_matches import ensure_tables as ensure_hash_tables
from backend.app.sources import ensure_tables as ensure_source_tables, annotate

def test_clean_search_query():
    assert clean_search_query("cool_lora_v1.0_fp16.safetensors") == "cool lora"
    assert clean_search_query("my-model-pruned-bf16.safetensors") == "my model"
    assert clean_search_query("Moss-image-lora-comfyui.safetensors") == "Moss image lora"
    assert clean_search_query("short.safetensors", meta_title="Real Cyberpunk") == "Real Cyberpunk"
    assert clean_search_query("model_fp16.onnx", full_path="C:/models/booru_tagger/pixai-tagger-v1.0-fp16/model_fp16.onnx") == "pixai tagger"

def test_suggest_matches_civitai_and_hf(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT,
        size_bytes INTEGER, modified_ns INTEGER, file_id TEXT, sha256 TEXT, weights_sha256 TEXT,
        trigger_words TEXT, base_model_guess TEXT, meta_title TEXT, PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_source_tables(connection)

    connection.execute("INSERT INTO models VALUES ('pc:comfyui', 'C:/m/cool.safetensors', 'cool.safetensors', 'lora', 5000, 1, NULL, ?, ?, NULL, 'SDXL', 'Cool Concept')", ('a' * 64, 'w' * 64))

    mock_cv = {
        "models": [{
            "id": 10, "name": "Cool Concept", "url": "https://civitai.com/models/10",
            "files": [{"verdict": "exact", "version_id": 20, "version_name": "v1", "file_id": 30, "file_name": "cool.safetensors"}]
        }]
    }
    mock_hf_repos = [{"id": "author/cool-lora"}]
    mock_hf_listing = {
        "files": [{"path": "cool.safetensors", "name": "cool.safetensors", "size_bytes": 5000, "sha256": "a" * 64, "url": "https://hf.co/..."}]
    }

    def fake_get(url):
        if "civitai.com" in url:
            return json.dumps(mock_cv).encode()
        if "api/models?" in url:
            return json.dumps(mock_hf_repos).encode()
        return b"{}"

    with patch("backend.app.suggest_matches.civitai_check", return_value=mock_cv):
        with patch("backend.app.suggest_matches.list_model_files", return_value=mock_hf_listing):
            res = suggest_matches(connection, "pc:comfyui", "C:/m/cool.safetensors", get=fake_get)

    assert res["query"] == "Cool Concept"
    assert len(res["civitai"]) == 1
    assert res["civitai"][0]["verdict"] == "exact"
    assert len(res["huggingface"]) == 1
    assert res["huggingface"][0]["verdict"] == "exact"
    assert res["huggingface"][0]["repository"] == "author/cool-lora"

def test_link_huggingface(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT,
        size_bytes INTEGER, modified_ns INTEGER, file_id TEXT, sha256 TEXT, weights_sha256 TEXT,
        trigger_words TEXT, base_model_guess TEXT, meta_title TEXT, PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_source_tables(connection)

    connection.execute("""INSERT INTO models VALUES
        ('pc:comfyui', 'C:/m/moss_lora.safetensors', 'moss_lora.safetensors', 'lora', 74000000, 100, NULL,
         'm' * 64, NULL, NULL, 'MOSS', NULL)""")

    listing = {
        "files": [{"path": "moss_lora.safetensors", "name": "moss_lora.safetensors", "size_bytes": 74000000, "sha256": "m" * 64}]
    }

    with patch("backend.app.suggest_matches.list_model_files", return_value=listing):
        res = link_huggingface(connection, "pc:comfyui", "C:/m/moss_lora.safetensors", "axiomofmind/moss-lora", "moss_lora.safetensors")

    assert res["ok"] is True
    row = connection.execute("SELECT repository, remote_path, sha256 FROM selected_matches WHERE path='C:/m/moss_lora.safetensors'").fetchone()
    assert row == ("axiomofmind/moss-lora", "moss_lora.safetensors", "m" * 64)
