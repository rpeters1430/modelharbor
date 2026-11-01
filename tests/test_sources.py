"""Offline coverage for source identification and update checks."""
import hashlib
import json
import sqlite3
import pytest
from backend.app.hash_matches import ensure_tables as ensure_hash_tables
from backend.app.intelligence import enrich
from backend.app.sources import annotate, ensure_tables, run_pass

SHA = "a" * 64

def files(digest, file_type="Model", file_id=1, name="cool.safetensors"):
    return [{"id": file_id, "type": file_type, "primary": True, "name": name, "sizeKB": 1.0,
             "hashes": {"SHA256": digest.upper()}}]

OLD = {"id": 10, "modelId": 7, "name": "v1.0", "baseModel": "Pony", "publishedAt": "2025-01-01T00:00:00Z",
       "trainedWords": ["trigger"], "nsfwLevel": 1, "model": {"name": "Cool LoRA", "nsfw": False},
       "files": files(SHA)}
MODEL = {"modelVersions": [
    {"id": 12, "name": "v2.0", "baseModel": "Pony", "publishedAt": "2025-06-01T00:00:00Z",
     "files": files("b" * 64, "UNet", 120, "cool_v2.safetensors")},
    {"id": 13, "name": "Illustrious v1", "baseModel": "Illustrious", "publishedAt": "2025-09-01T00:00:00Z",
     "files": files("c" * 64)},
    {"id": 10, "name": "v1.0", "baseModel": "Pony", "publishedAt": "2025-01-01T00:00:00Z", "files": files(SHA)}]}
MANIFEST = b'{"schemaVersion":2}'

@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT,
        size_bytes INTEGER, modified_ns INTEGER, file_id TEXT, sha256 TEXT, weights_sha256 TEXT, PRIMARY KEY(device,path))""")
    ensure_hash_tables(connection)
    ensure_tables(connection)
    yield connection
    connection.close()

def add(db, path, kind="lora", sha=SHA, name=None, weights=None):
    db.execute("INSERT INTO models VALUES (?,?,?,?,?,?,?,?,?)",
               ("pc:comfyui", path, name or path.rsplit("/", 1)[-1], kind, 100, 1, None, sha, weights))

def records(db):
    rows = db.execute("SELECT device,path,name,kind,size_bytes,modified_ns,file_id,sha256 FROM models").fetchall()
    fields = ("device", "path", "name", "kind", "size_bytes", "modified_ns", "file_id", "sha256")
    return enrich(annotate(db, [dict(zip(fields, r)) for r in rows]))

def fake(responses, calls):
    def get(url, accept="application/json"):
        calls.append(url)
        body = responses.get(url)
        return body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
    return get

def test_civitai_identity_and_same_base_update(db):
    add(db, "C:/m/loras/Pony/cool.safetensors")
    calls = []
    run_pass(db, fake({f"https://civitai.com/api/v1/model-versions/by-hash/{SHA}": OLD,
                       "https://civitai.com/api/v1/models/7": MODEL}, calls), pause=0)
    item = records(db)[0]
    assert item["source"]["model_name"] == "Cool LoRA" and item["source"]["trained_words"] == ["trigger"]
    assert item["source"]["url"] == "https://civitai.com/models/7?modelVersionId=10"
    # The newer Illustrious version is a variant; the Pony v2.0 is the update.
    assert item["update"]["version_name"] == "v2.0" and item["update"]["version_id"] == 12
    # "Model" and "UNet" are both the main weights; the exact file and hash to fetch are recorded.
    stored = json.loads(db.execute("SELECT latest_file FROM civitai_files").fetchone()[0])
    assert stored == {"id": 120, "name": "cool_v2.safetensors", "sha256": "b" * 64, "size_bytes": 1024}
    assert (item["family_guess"], item["family_verified"]) == ("Pony", True)

def test_falls_back_to_civitai_red_and_links_mature_models_there(db):
    add(db, "C:/m/loras/x.safetensors")
    mature = {**OLD, "nsfwLevel": 16, "model": {"name": "Mature", "nsfw": True}}
    calls = []
    run_pass(db, fake({f"https://civitai.red/api/v1/model-versions/by-hash/{SHA}": mature,
                       "https://civitai.com/api/v1/models/7": {"modelVersions": [OLD]}}, calls), pause=0)
    item = records(db)[0]
    assert calls[:2] == [f"https://civitai.com/api/v1/model-versions/by-hash/{SHA}",
                         f"https://civitai.red/api/v1/model-versions/by-hash/{SHA}"]
    assert item["source"]["url"].startswith("https://civitai.red/models/7")
    assert item["update"] is None

def test_unknown_hash_is_not_retried_every_pass(db):
    add(db, "C:/m/loras/private.safetensors")
    calls = []
    run_pass(db, fake({}, calls), pause=0, now=1_000_000)
    run_pass(db, fake({}, calls), pause=0, now=1_000_100)
    assert len(calls) == 2  # both hosts once, then cached as not found
    assert records(db)[0]["source"] is None and records(db)[0]["source_checked"] is True

def test_ollama_update_from_manifest_digest(db):
    current = hashlib.sha256(MANIFEST).hexdigest()
    add(db, "ollama://qwen3:8b", kind="ollama/qwen3", sha=current, name="qwen3:8b")
    add(db, "ollama://huihui_ai/gemma:12b", kind="ollama/gemma", sha="b" * 64, name="huihui_ai/gemma:12b")
    add(db, "ollama://huggingface.co/a/b:latest", kind="ollama/x", sha="c" * 64, name="huggingface.co/a/b:latest")
    calls = []
    run_pass(db, fake({"https://registry.ollama.ai/v2/library/qwen3/manifests/8b": MANIFEST,
                       "https://registry.ollama.ai/v2/huihui_ai/gemma/manifests/12b": MANIFEST}, calls), pause=0)
    by_name = {r["name"]: r for r in records(db)}
    assert by_name["qwen3:8b"]["update"] is None
    assert by_name["huihui_ai/gemma:12b"]["update"]["url"] == "https://ollama.com/huihui_ai/gemma"
    assert by_name["huggingface.co/a/b:latest"]["source"] is None
    assert not any("huggingface.co/a" in url for url in calls)

def test_selected_huggingface_match_reports_changed_file(db):
    add(db, "C:/m/loras/hf.safetensors", sha=None)
    db.execute("""INSERT INTO selected_matches (device,path,repository,remote_path,sha256,local_size,
        local_modified_ns,selected_at) VALUES ('pc:comfyui','C:/m/loras/hf.safetensors','o/r','hf.safetensors',?,100,1,0)""", (SHA,))
    run_pass(db, fake({}, []), list_files=lambda repo: {"files": [{"path": "hf.safetensors", "sha256": "d" * 64}]}, pause=0)
    item = records(db)[0]
    assert item["source"]["provider"] == "huggingface"
    assert item["update"]["provider"] == "huggingface"

def test_family_folder_beats_filename(db):
    add(db, "C:/m/checkpoints/SDXL/ponyish.safetensors", kind="checkpoint", sha=None)
    item = records(db)[0]
    assert (item["family_guess"], item["family_verified"]) == ("SDXL", False)

def test_variants_are_not_updates():
    from backend.app.sources import newer_version
    versions = lambda *names: [{"id": i, "name": n, "baseModel": "SDXL 1.0", "files": files("e" * 64)}
                               for i, n in enumerate(names)]
    assert newer_version("V0.1", "SDXL 1.0", versions("V0.1", "V0.4"), "Model")["name"] == "V0.4"
    dated = [{"name": "V0.25", "baseModel": "B", "publishedAt": "2025-01-01", "files": files("e" * 64)},
             {"name": "V0.4", "baseModel": "B", "publishedAt": "2025-05-01", "files": files("e" * 64)}]
    assert newer_version("V0.1", "B", dated, "Model")["name"] == "V0.4"
    assert newer_version("Turbo v1.0", "SDXL 1.0", versions("Turbo v3.0", "Turbo v2.0"), "Model")["name"] == "Turbo v3.0"
    for local, other in (("v7.0", "v6.0 - Inpainting"), ("v7.0 Photo XL", "v7.0 Photo XL DMD"),
                         ("4 Steps", "8 Steps"), ("v1.5 fp8", "v1.5 INT8"), ("v2.1", "v2.1 Turbo"),
                         ("Base", "SFT"), ("v1.0", "v1.0-inpainting")):
        assert newer_version(local, "SDXL 1.0", versions(local, other), "Model") is None, (local, other)
    assert newer_version("v1.0", "Pony", versions("v2.0"), "Model") is None  # other base model

def test_text_encoder_is_not_replaced_by_diffusion_model():
    from backend.app.sources import newer_version
    v3 = {"name": "Turbo v3.0", "baseModel": "Krea 2", "files": files("e" * 64, "Model")}
    assert newer_version("Turbo v1.0", "Krea 2", [v3], "Text Encoder") is None
    assert newer_version("Turbo v1.0", "Krea 2", [v3], "Diffusion Model")["name"] == "Turbo v3.0"

def test_unknown_file_type_gets_no_update():
    from backend.app.sources import newer_version
    assert newer_version("V0.1", "B", [{"name": "V0.4", "baseModel": "B", "files": files("e" * 64)}], None) is None

def test_manual_link_exact_and_unverified(db):
    from backend.app.sources import civitai_link, civitai_unlink, civitai_versions
    add(db, "C:/m/loras/cool.safetensors")
    model = {"name": "Cool LoRA", "nsfw": False, "modelVersions": MODEL["modelVersions"]}
    get = fake({"https://civitai.com/api/v1/models/7": model}, [])
    picker = civitai_versions(7, SHA, get)
    exact = [(v["name"], f["id"]) for v in picker["versions"] for f in v["files"] if f["exact"]]
    assert exact == [("v1.0", 1)]
    assert civitai_link(db, SHA, 7, 10, 1, get) == {"verified": True, "same_weights": False}
    item = records(db)[0]
    assert item["source"]["verified"] and item["update"]["version_name"] == "v2.0"  # update computed at once
    # Linking to a file with another hash is kept, marked unverified, and still tracks updates.
    assert civitai_link(db, SHA, 7, 13, 999, get) is None  # file 999 is not in version 13
    model["modelVersions"][2]["files"] = files("f" * 64, "Model", 9)
    assert civitai_link(db, SHA, 7, 10, 9, get) == {"verified": False, "same_weights": False}
    item = records(db)[0]
    assert item["source"]["verified"] is False and item["update"]["version_name"] == "v2.0"
    run_pass(db, fake({}, []), pause=0)  # a manual link is not overwritten by the hash lookup
    assert records(db)[0]["source"]["verified"] is False
    civitai_unlink(db, SHA)
    assert records(db)[0]["source"] is None

WEIGHTS = "d" * 64

def test_edited_metadata_copy_is_found_by_weights_hash(db):
    """A file whose embedded metadata changed has a new SHA-256 but the same tensors (Civitai AutoV3)."""
    add(db, "loras/edited.safetensors", sha="e" * 64)
    version = {**OLD, "files": [{**files(SHA)[0], "hashes": {"SHA256": SHA.upper(), "AutoV3": WEIGHTS[:12].upper()}}]}
    asked = []
    def get(url, accept="application/json"):
        asked.append(url.rsplit("/", 1)[-1])
        return json.dumps(version).encode() if url.endswith("/" + WEIGHTS[:12]) else None
    run_pass(db, get, now=1000, pause=0)
    assert "e" * 64 in asked and WEIGHTS[:12] not in asked  # weights hash not known yet
    db.execute("UPDATE models SET weights_sha256=?", (WEIGHTS,))
    run_pass(db, get, now=2000, pause=0)  # tried again as soon as the weights hash arrives
    record = annotate(db, [{"device": "pc:comfyui", "path": "loras/edited.safetensors", "name": "edited.safetensors",
                            "kind": "lora", "sha256": "e" * 64}])[0]
    assert record["source"]["model_name"] == "Cool LoRA" and record["source"]["weights_match"]
    asked.clear()
    run_pass(db, get, now=3000, pause=0)
    assert WEIGHTS[:12] not in asked and "e" * 64 not in asked  # found, so not looked up again

def test_check_judges_every_search_result(db):
    from backend.app.sources import civitai_check
    def f(file_id, sha, kb, autov3=""):
        return {"id": file_id, "name": f"f{file_id}.safetensors", "sizeKB": kb, "hashes": {"SHA256": sha.upper(), "AutoV3": autov3}}
    page = {"items": [
        {"id": 1, "name": "Other", "modelVersions": [{"id": 11, "name": "v1", "files": [f(1, "1" * 64, 900000)]}]},
        {"id": 2, "name": "Same size", "modelVersions": [{"id": 21, "name": "v1", "files": [f(2, "2" * 64, 1.0)]}]},
        {"id": 3, "name": "Mine", "modelVersions": [{"id": 31, "name": "v1", "files": [f(3, "3" * 64, 2.0, WEIGHTS[:12].upper())]}]}]}
    result = civitai_check("cool", SHA, WEIGHTS, 1024, lambda url, accept=None: json.dumps(page).encode())
    assert result["checked_files"] == 3
    assert [(m["name"], m["best"]) for m in result["models"]] == [("Mine", "same_weights"), ("Same size", "same_size"), ("Other", None)]
    assert result["models"][0]["files"][0]["file_id"] == 3 and result["models"][2]["sizes"] == [921600000]
