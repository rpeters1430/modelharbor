"""Browse marks files already on the PC by hash and queues new downloads into fixed folders."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from download_worker import download_file
from backend.app import browse, downloads

MINE, OTHER = "a" * 64, "b" * 64

def civitai_file(file_id, sha, file_type="Model", name="m.safetensors"):
    return {"id": file_id, "type": file_type, "name": name, "sizeKB": 2.0, "primary": True, "hashes": {"SHA256": sha.upper()}}

PAGE = {"items": [
    {"id": 1, "name": "Cool LoRA", "type": "LORA", "nsfw": False, "stats": {"downloadCount": 9},
     "modelVersions": [
         {"id": 12, "name": "v2", "baseModel": "Pony", "files": [civitai_file(120, OTHER)],
          "images": [{"url": "https://image.civitai.com/x.jpg", "nsfwLevel": 1}]},
         {"id": 11, "name": "v1", "baseModel": "Pony", "files": [civitai_file(110, MINE)]}]},
    {"id": 2, "name": "Big Checkpoint", "type": "Checkpoint", "nsfw": True,
     "modelVersions": [{"id": 21, "name": "v1", "baseModel": "SDXL 1.0",
                        "files": [civitai_file(210, OTHER), civitai_file(211, OTHER, "VAE", "vae.safetensors")]}]}],
    "metadata": {"nextCursor": "20|abc"}}

def test_search_marks_installed_versions_and_folders():
    seen = []
    def get(url, accept="application/json"):
        seen.append(url)
        return json.dumps(PAGE).encode()
    result = browse.search_civitai("cool", "LORA", "Pony", "Newest", "", {MINE}, get)
    query = parse_qs(urlsplit(seen[0]).query)
    assert query["types"] == ["LORA"] and query["nsfw"] == ["true"] and query["baseModels"] == ["Pony"]
    lora, checkpoint = result["items"]
    assert (lora["installed_version"], lora["latest_installed"]) == ("v1", False)  # older version on the PC
    assert [f["on_pc"] for v in lora["versions"] for f in v["files"]] == [False, True]
    assert lora["image"] == {"url": "https://image.civitai.com/x.jpg", "nsfw_level": 1}
    assert [f["folder"] for f in checkpoint["versions"][0]["files"]] == ["checkpoints/SDXL", "vae"]
    assert checkpoint["url"].startswith("https://civitai.red/") and checkpoint["installed_version"] is None
    assert result["next_cursor"] == "20|abc"

def test_search_rejects_bad_input():
    for args in (("x", "Script", "", "Newest", ""), ("x", "", "", "Random", ""), ("x", "", "", "Newest", "a b<>")):
        with pytest.raises(HTTPException):
            browse.search_civitai(*args, set(), lambda url, accept=None: b"{}")

def test_huggingface_browse_lists_top_without_query():
    urls = []
    def get(url):
        urls.append(url)
        return json.dumps([{"id": "a/lora", "tags": ["lora"], "downloads": 5, "likes": 2},
                           {"id": "b/model", "tags": []}, {"id": ""}]).encode()
    items = browse.search_huggingface("", "lora", "Trending", get)["items"]
    assert [(i["id"], i["folder"]) for i in items] == [("a/lora", "loras"), ("b/model", "checkpoints")]
    assert "search=" not in urls[0] and "filter=lora" in urls[0] and "sort=trendingScore" in urls[0]
    browse.search_huggingface("flux", "", "Newest", get)
    assert "search=flux" in urls[1] and "sort=createdAt" in urls[1]
    for bad in (("x", "nope", "Trending"), ("x", "", "Random"), ("x" * 121, "", "Trending")):
        with pytest.raises(HTTPException):
            browse.search_huggingface(*bad, get)

def test_modelscope_browse_and_files():
    urls = []
    def get(url):
        urls.append(url)
        if "/openapi/" in url:
            return json.dumps({"data": {"total_count": 31, "models": [{"id": "a/style_lora"}, {"id": "../x"}]}}).encode()
        return json.dumps({"Data": {"Files": [
            {"Type": "blob", "Path": "sub/w.safetensors", "Sha256": MINE.upper(), "Size": 5},
            {"Type": "blob", "Path": "README.md", "Sha256": MINE}, {"Type": "tree", "Path": "sub"},
            {"Type": "blob", "Path": "nohash.gguf", "Sha256": ""}]}}).encode()
    page = browse.search_modelscope("", "", "Most Downloaded", 1, get)
    assert [(i["id"], i["folder"]) for i in page["items"]] == [("a/style_lora", "loras")] and page["next_cursor"] == "2"
    assert "filter.task=text-to-image-synthesis" in urls[0] and "search=" not in urls[0]
    assert browse.search_modelscope("x", "", "Most Liked", 2, get)["next_cursor"] is None
    for bad in (("x", "lora", "Most Liked", 1), ("x", "", "Trending", 1), ("x", "", "Most Liked", 0)):
        with pytest.raises(HTTPException):
            browse.search_modelscope(*bad, get)
    files = browse.list_modelscope_files("a/style_lora", get)["files"]
    assert [(f["path"], f["sha256"]) for f in files] == [("nohash.gguf", None), ("sub/w.safetensors", MINE)]
    assert browse.modelscope_url("a/b", "sub/w x.safetensors") == "https://modelscope.cn/models/a/b/resolve/master/sub/w%20x.safetensors"
    with pytest.raises(HTTPException):
        browse.list_modelscope_files("../etc", get)

def test_family_folders_follow_the_base_model():
    assert browse.folder_for("LORA", None, "SDXL 1.0") == "loras/SDXL"
    assert browse.folder_for("LORA", None, "Illustrious") == "loras/SDXL"
    assert browse.folder_for("Checkpoint", "Model", "SD 1.5") == "checkpoints/SD15"
    assert browse.folder_for("Checkpoint", "UNet", "Krea 2 Turbo") == "diffusion_models/Krea2"
    assert browse.folder_for("LORA", None, "Flux.1 Krea") == "loras/Flux"
    assert browse.folder_for("LORA", None, "Pony V7") == "loras/PonyV7"
    assert browse.folder_for("Checkpoint", "VAE", "SDXL 1.0") == "vae"  # no family folders for VAEs
    assert browse.folder_for("LORA", None, "Other") == browse.folder_for("LORA", None, None) == "loras"
    assert browse.valid_folder("loras/SDXL") and browse.valid_folder("vae")
    assert not any(map(browse.valid_folder, ("vae/SDXL", "loras/../x", "loras/Nope", "nope", "")))

def test_huggingface_listing_is_marked():
    listing = {"files": [{"path": "a.safetensors", "sha256": MINE}, {"path": "b.safetensors", "sha256": None}]}
    assert [f["on_pc"] for f in browse.mark_huggingface(listing, {MINE})["files"]] == [True, False]

@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    downloads.ensure_tables(connection)
    connection.execute("CREATE TABLE agent_status (device TEXT PRIMARY KEY, phase TEXT NOT NULL, scan_requested_at INTEGER)")
    yield connection
    connection.close()

def test_new_download_queue(db):
    args = ("https://civitai.com/api/download/models/12?fileId=120", "cool_v2.safetensors", OTHER, 2048, "loras", "civitai:120", "civitai")
    job = downloads.create_new(db, "pc", *args)
    assert downloads.create_new(db, "pc", *args)["id"] == job["id"]  # no duplicate while queued
    claimed = downloads.claim(db, "pc")
    assert (claimed["target_folder"], claimed["path"]) == ("loras", "")
    downloads.finish(db, job["id"], "pc", OTHER, None, "C:/m/loras/cool_v2.safetensors")
    assert db.execute("SELECT scan_requested_at FROM agent_status WHERE device='pc'").fetchone()[0]  # rescan
    for bad in ({"folder": "..\\Windows"}, {"name": "x.exe"}, {"sha": None}):
        with pytest.raises(HTTPException):
            downloads.create_new(db, "pc", args[0], bad.get("name", "ok.safetensors"), bad.get("sha", OTHER), 1,
                                 bad.get("folder", "loras"), "civitai:9", "civitai")

class FakeResponse:
    status_code, headers = 200, {"Content-Type": "application/octet-stream"}
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def iter_content(self, size):
        yield self.body

class FakeSession:
    def get(self, url, **kwargs):
        return FakeResponse(b"weights")

def test_agent_saves_new_download_in_target_folder(tmp_path):
    job = {"path": "", "target_folder": "loras", "url": "https://civitai.com/api/download/models/12?fileId=120",
           "file_name": "cool_v2.safetensors", "size_bytes": 7, "expected_sha": hashlib.sha256(b"weights").hexdigest()}
    sha, path = download_file(FakeSession(), job, [str(tmp_path)], lambda done: None)
    assert Path(path) == tmp_path / "loras" / "cool_v2.safetensors" and Path(path).read_bytes() == b"weights"
    with pytest.raises(ValueError, match="folder is not allowed"):
        download_file(FakeSession(), {**job, "target_folder": "..", "file_name": "x.safetensors"}, [str(tmp_path)], lambda d: None)
    for bad in ("loras/..", "loras/a/b", "vae2/SDXL"):
        with pytest.raises(ValueError):
            download_file(FakeSession(), {**job, "target_folder": bad, "file_name": "y.safetensors"}, [str(tmp_path)], lambda d: None)
