"""Hardware suggestions in Browse, and deleting a model file from a PC."""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend.app import deletes, downloads, fit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from download_worker import delete_files

GB = 1024**3
AMD = {"gpu": "AMD Radeon RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * GB, "ram_bytes": 128 * GB}


def test_rate_for_amd_16gb():
    assert fit.rate("lora.safetensors", 200 * 1024**2, AMD)["level"] == "good"
    assert fit.rate("flux1-dev-nvfp4.safetensors", 6 * GB, AMD)["level"] == "bad"
    assert fit.rate("flux1-dev-nvfp4.safetensors", 6 * GB, {**AMD, "vendor": "nvidia"})["level"] == "good"
    assert "offloading" in fit.rate("wan_14B_fp16.safetensors", 28 * GB, AMD)["note"]
    assert fit.rate("huge.safetensors", 80 * GB, AMD)["level"] == "bad"
    assert fit.rate("old.ckpt", 2 * GB, AMD)["level"] == "ok"
    assert fit.rate("m.safetensors", 4 * GB, AMD, {"fp": "fp32", "format": "SafeTensor"})["level"] == "ok"
    assert fit.rate("m.safetensors", 4 * GB, None) is None
    assert fit.rate("dreamshaper_8Diffusers.zip", 5 * GB, AMD)["level"] == "bad"


def test_best_variant_is_largest_that_fits():
    files = [{"path": f"split/wan_14B_{q}.{ext}", "name": f"wan_14B_{q}.{ext}", "size_bytes": size * GB}
             for q, ext, size in (("fp16", "safetensors", 28), ("fp8_scaled", "safetensors", 14),
                                  ("Q8_0", "gguf", 15), ("Q4_K_M", "gguf", 9))]
    files.append({"path": "split/vae.safetensors", "name": "vae.safetensors", "size_bytes": GB // 4})
    fit.annotate_listing({"files": files}, AMD)
    assert [f["name"] for f in files if f.get("best")] == ["wan_14B_fp8_scaled.safetensors"]
    # Nothing fits in VRAM: the smallest copy that runs.
    small = {**AMD, "vram_bytes": 8 * GB}
    files = [{**f, "best": False} for f in files]
    fit.annotate_listing({"files": files}, small)
    assert [f["name"] for f in files if f.get("best")] == ["wan_14B_Q4_K_M.gguf"]


def test_civitai_marks_best_pruned_copy():
    from backend.app import browse
    def f(i, name, kb, fp, size, kind="Model"):
        return {"id": i, "name": name, "type": kind, "sizeKB": kb, "hashes": {"SHA256": str(i) * 64},
                "metadata": {"fp": fp, "size": size, "format": "SafeTensor"}}
    page = {"items": [{"id": 1, "name": "Check", "type": "Checkpoint", "modelVersions": [{"id": 2, "name": "v1",
            "baseModel": "SD 1.5", "files": [f(3, "full.safetensors", 4 * 1024**2, "fp32", "full"),
                                             f(4, "pruned.safetensors", 2 * 1024**2, "fp16", "pruned"),
                                             f(5, "vae.safetensors", 300 * 1024, "fp16", "pruned", "VAE")]}]}]}
    result = browse.search_civitai("", "", "", "Most Downloaded", "", set(), lambda url: json.dumps(page).encode(), hw=AMD)
    files = result["items"][0]["versions"][0]["files"]
    assert [(x["name"], bool(x.get("best"))) for x in files] == [("full.safetensors", False), ("pruned.safetensors", True),
                                                                 ("vae.safetensors", False)]


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT, size_bytes INTEGER,
        modified_ns INTEGER, file_id TEXT, sha256 TEXT, PRIMARY KEY(device,path))""")
    connection.execute("CREATE TABLE agent_status (device TEXT PRIMARY KEY, phase TEXT NOT NULL, scan_requested_at INTEGER)")
    downloads.ensure_tables(connection)
    deletes.ensure_tables(connection)
    yield connection
    connection.close()


def test_delete_takes_hard_links_and_updates_inventory(db):
    rows = [("pc:comfyui", "C:/m/loras/a.safetensors", "1:9"), ("pc:comfyui", "C:/m/loras/SDXL/a.safetensors", "1:9"),
            ("pc:comfyui", "C:/m/loras/b.safetensors", "1:7")]
    db.executemany("INSERT INTO models VALUES (?,?,'n','lora',5,1,?,?)", [(d, p, i, "a" * 64) for d, p, i in rows])
    job = deletes.request(db, "pc:comfyui", "C:/m/loras/a.safetensors")
    assert job["paths"] == ["C:/m/loras/SDXL/a.safetensors", "C:/m/loras/a.safetensors"] and job["agent"] == "pc"
    assert deletes.request(db, "pc:comfyui", "C:/m/loras/SDXL/a.safetensors")["id"] == job["id"]  # no duplicate
    with pytest.raises(HTTPException):
        deletes.request(db, "pc:ollama", "llama3:8b")
    claimed = deletes.claim(db, "pc")
    assert claimed["sha256"] == "a" * 64 and deletes.claim(db, "pc") is None
    with pytest.raises(HTTPException):
        deletes.finish(db, job["id"], "other-pc", None)
    assert deletes.finish(db, job["id"], "pc", None)["state"] == "done"
    assert [r[0] for r in db.execute("SELECT path FROM models")] == ["C:/m/loras/b.safetensors"]
    assert db.execute("SELECT scan_requested_at FROM agent_status WHERE device='pc'").fetchone()[0]


def test_agent_deletes_only_the_inventoried_file(tmp_path):
    loras = tmp_path / "loras"
    (loras / "SDXL").mkdir(parents=True)
    model = loras / "a.safetensors"
    model.write_bytes(b"weights")
    os.link(model, loras / "SDXL" / "a.safetensors")
    (loras / "a.preview.png").write_bytes(b"png")
    (loras / "a.metadata.json").write_text("{}")
    (loras / "keep.safetensors").write_bytes(b"other")
    job = {"paths": [str(model), str(loras / "SDXL" / "a.safetensors")], "sha256": hashlib.sha256(b"weights").hexdigest()}
    with pytest.raises(ValueError):
        delete_files({**job, "sha256": "0" * 64}, [str(tmp_path)])  # changed file: nothing deleted
    with pytest.raises(ValueError):
        delete_files({**job, "paths": [str(model), str(loras / "keep.safetensors")]}, [str(tmp_path)])  # not one file
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(ValueError):
        delete_files(job, [str(tmp_path / "elsewhere")])  # outside the ComfyUI roots
    assert model.exists()
    delete_files(job, [str(tmp_path)])
    assert sorted(p.name for p in loras.rglob("*") if p.is_file()) == ["keep.safetensors"]


def test_comfy_org_quant_names_group_together():
    names = [("diffusion_models/qwen_image_2.1_bf16.safetensors", 14.2), ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", 7.3),
             ("text_encoders/qwen3vl_8b_bf16.safetensors", 17.5), ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", 9.4),
             ("text_encoders/qwen3vl_8b_w4a8.safetensors", 6.3)]
    files = [{"path": p, "name": p.rsplit("/", 1)[1], "size_bytes": int(s * GB)} for p, s in names]
    fit.annotate_listing({"files": files}, AMD)
    assert [f["name"] for f in files if f.get("best")] == ["qwen_image_2.1_int8_convrot.safetensors", "qwen3vl_8b_int8_convrot.safetensors"]
