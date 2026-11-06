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
        trigger_words TEXT, base_model_guess TEXT, meta_title TEXT, checkpoint TEXT, update_available INTEGER,
        PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_source_tables(connection)

    connection.execute("INSERT INTO models VALUES ('pc:comfyui', 'C:/m/cool.safetensors', 'cool.safetensors', 'lora', 5000, 1, NULL, ?, ?, NULL, 'SDXL', 'Cool Concept', NULL, NULL)", ('a' * 64, 'w' * 64))

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


def _lemonade_db():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT,
        size_bytes INTEGER, modified_ns INTEGER, file_id TEXT, sha256 TEXT, weights_sha256 TEXT,
        trigger_words TEXT, base_model_guess TEXT, meta_title TEXT, checkpoint TEXT, update_available INTEGER,
        PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_source_tables(connection)
    return connection

def test_lemonade_suggestions_use_the_real_file_and_the_repo_in_its_name():
    connection = _lemonade_db()
    name = "hf.co/mradermacher/Coder-GGUF:Q4_K_S"
    size = int(16.3 * 2**30)  # Lemonade rounds to 0.01 GiB
    connection.execute("INSERT INTO models (device,path,name,kind,size_bytes,modified_ns,checkpoint) VALUES (?,?,?,?,?,0,?)",
                       ("pc:lemonade", "lemonade://" + name, name, "lemonade/llamacpp", size, r"C:\AI\chat\Coder-Q4_K_S.gguf"))
    listing = {"files": [
        {"path": "Coder.Q4_K_S.gguf", "name": "Coder.Q4_K_S.gguf", "size_bytes": size + 2_000_000, "sha256": "b" * 64},
        {"path": "Coder.Q8_0.gguf", "name": "Coder.Q8_0.gguf", "size_bytes": 2 * size, "sha256": "c" * 64},
        {"path": "mmproj-f16.gguf", "name": "mmproj-f16.gguf", "size_bytes": size, "sha256": "d" * 64}]}
    calls = []
    def fake_list(repo):
        calls.append(repo)
        return listing
    with patch("backend.app.suggest_matches.civitai_check") as civitai, \
         patch("backend.app.suggest_matches.list_model_files", side_effect=fake_list):
        res = suggest_matches(connection, "pc:lemonade", "lemonade://" + name, get=lambda url: b"[]")
    civitai.assert_not_called()  # LLMs are not on Civitai
    assert calls == ["mradermacher/Coder-GGUF"]
    # The exact file first; sibling quants of the same model follow as name-only matches.
    assert [(h["remote_path"], h["verdict"]) for h in res["huggingface"]] == [
        ("Coder.Q4_K_S.gguf", "same_weights"), ("Coder.Q8_0.gguf", "same_name")]

def test_lemonade_models_link_automatically_by_hand_and_as_local_only(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "lemonade.sqlite3")
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "test-token"}
    models = [
        {"device": "pc:lemonade", "path": "lemonade://Qwen3-0.6B-GGUF", "name": "Qwen3-0.6B-GGUF", "kind": "lemonade/llamacpp",
         "size_bytes": 382_000_000, "modified_ns": 0, "checkpoint": "unsloth/Qwen3-0.6B-GGUF:Q4_0", "update_available": True},
        {"device": "pc:lemonade", "path": "lemonade://gemma4-12b", "name": "gemma4-12b", "kind": "lemonade/llamacpp",
         "size_bytes": 7_000_000_000, "modified_ns": 0, "checkpoint": r"C:\AI\chat\gemma4-12b.gguf"}]
    assert client.post("/api/inventory", headers=headers, json={"device": "pc:lemonade", "models": models}).status_code == 200
    by_name = {m["name"]: m for m in client.get("/api/models", headers=headers).json()}
    auto = by_name["Qwen3-0.6B-GGUF"]
    assert auto["source"]["model_name"] == "unsloth/Qwen3-0.6B-GGUF" and auto["source"]["reported_by"] == "lemonade"
    assert auto["update"]["provider"] == "lemonade"
    assert client.post("/api/downloads", headers=headers, json={"device": "pc:lemonade", "path": auto["path"]}).status_code == 409
    assert by_name["gemma4-12b"]["source"] is None
    # Local only, keyed by device and path because Lemonade models have no hash.
    ref = {"device": "pc:lemonade", "path": "lemonade://gemma4-12b"}
    assert client.post("/api/match-ignore", headers=headers, json={**ref, "ignored": True}).status_code == 200
    assert next(m for m in client.get("/api/models", headers=headers).json() if m["name"] == "gemma4-12b")["match_ignored"] is True
    listing = {"files": [{"path": "gemma-4-12b.gguf", "name": "gemma-4-12b.gguf", "size_bytes": 7_000_000_000, "sha256": "e" * 64}]}
    with patch("backend.app.suggest_matches.list_model_files", return_value=listing):
        assert client.post("/api/huggingface/link", headers=headers,
                           json={**ref, "repository": "google/gemma-4-12b-GGUF", "remote_path": "gemma-4-12b.gguf"}).status_code == 200
    linked = next(m for m in client.get("/api/models", headers=headers).json() if m["name"] == "gemma4-12b")
    assert linked["source"]["model_name"] == "google/gemma-4-12b-GGUF" and linked["match_ignored"] is False
    assert client.post("/api/huggingface/unlink", headers=headers, json=ref).status_code == 200
    assert next(m for m in client.get("/api/models", headers=headers).json() if m["name"] == "gemma4-12b")["source"] is None
