"""Converting a model that does not fit the GPU, and recognising the user's own conversions."""
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend.app import converts, downloads, fit
from backend.app.hash_matches import ensure_tables as ensure_hash_tables
from backend.app.sources import _verdict, annotate, ensure_tables as ensure_source_tables

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from convert_worker import convert, target_name

SRC, NEW = "a" * 64, "b" * 64


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE models (device TEXT, path TEXT, name TEXT, kind TEXT, size_bytes INTEGER,
        modified_ns INTEGER, file_id TEXT, sha256 TEXT, PRIMARY KEY(device,path))""")
    connection.execute("CREATE TABLE agent_status (device TEXT PRIMARY KEY, phase TEXT NOT NULL, scan_requested_at INTEGER)")
    for ensure in (downloads.ensure_tables, ensure_source_tables, ensure_hash_tables, converts.ensure_tables):
        ensure(connection)
    yield connection
    connection.close()


def test_conversion_inherits_the_source_identity(db):
    path = "C:/m/diffusion_models/Krea2/sick_v40_bf16.safetensors"
    db.executemany("INSERT INTO models VALUES ('pc:comfyui',?,?,?,24,1,NULL,?)",
                   [(path, "sick_v40_bf16.safetensors", "diffusion", SRC),
                    ("C:/m/loras/l.safetensors", "l.safetensors", "lora", "c" * 64),
                    ("C:/m/diffusion_models/x_int8_convrot.safetensors", "x_int8_convrot.safetensors", "diffusion", "d" * 64),
                    ("C:/m/diffusion_models/new.safetensors", "new.safetensors", "diffusion", None)])
    db.execute("""INSERT INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,version_name,base_model)
        VALUES (?,1,1,10,20,'SickRealism','v4.0','Krea 2')""", (SRC,))
    for bad in ("C:/m/loras/l.safetensors", "C:/m/diffusion_models/x_int8_convrot.safetensors",
                "C:/m/diffusion_models/new.safetensors"):
        with pytest.raises(HTTPException):
            converts.request(db, "pc:comfyui", bad)
    job = converts.request(db, "pc:comfyui", path)
    assert job["agent"] == "pc" and converts.request(db, "pc:comfyui", path)["id"] == job["id"]  # no duplicate
    assert converts.claim(db, "pc") == {"id": job["id"], "path": path, "sha256": SRC} and converts.claim(db, "pc") is None
    with pytest.raises(HTTPException):
        converts.finish(db, job["id"], "other-pc", None)
    new_path = path.replace("bf16", "int8_convrot")
    done = converts.finish(db, job["id"], "pc", None,
                           {"path": new_path, "sha256": NEW, "size_bytes": 12, "modified_ns": 5})
    assert done["state"] == "done" and done["result_sha"] == NEW
    record = {"device": "pc:comfyui", "path": new_path, "name": "x", "kind": "diffusion", "sha256": NEW}
    source = annotate(db, [record])[0]["source"]
    assert (source["model_name"], source["base_model"], source["verified"]) == ("SickRealism", "Krea 2", False)
    assert db.execute("SELECT scan_requested_at FROM agent_status WHERE device='pc'").fetchone()[0]


def test_failed_conversion_keeps_its_reason(db):
    db.execute("INSERT INTO models VALUES ('pc:comfyui','C:/m/w.safetensors','w.safetensors','text_encoder',1,1,NULL,?)", (SRC,))
    job = converts.request(db, "pc:comfyui", "C:/m/w.safetensors")
    converts.claim(db, "pc")
    failed = converts.finish(db, job["id"], "pc", "Not enough free disk space for the converted copy")
    assert failed["state"] == "failed" and "disk space" in failed["error"]


def test_own_conversion_is_recognised_by_name():
    assert fit.conversion_of("krea2Sickrealism_v40_int8_convrot.safetensors", "krea2Sickrealism_v40.gguf")
    # Civitai appends the version's name to the file name.
    assert fit.conversion_of("BSSDesirexKrea2_x3_int8_convrot.safetensors", "BSSDesirexKrea2_x3BF16INT8NVFP4_int8.safetensors")
    assert not fit.conversion_of("BSSDesirexKrea2_x3.safetensors", "BSSDesirexKrea2_x3BF16.safetensors")  # not a conversion
    assert not fit.conversion_of("flux_fp8.safetensors", "flux1-dev.safetensors")  # too short to be sure
    item = {"name": "snofsSexNudesAndOther_krea2RawV14_bf16.safetensors", "hashes": {"SHA256": NEW}, "sizeKB": 1}
    assert _verdict(item, SRC, None, 99, "snofsSexNudesAndOther_krea2RawV14_int8_convrot.safetensors") == "converted"
    assert _verdict(item, NEW, None, 99, "snofsSexNudesAndOther_krea2RawV14_int8_convrot.safetensors") == "exact"


def test_target_name():
    assert target_name("wan2.1_t2v_1.3B_bf16.safetensors") == "wan2.1_t2v_1.3B_int8_convrot.safetensors"
    assert target_name("museV4.safetensors") == "museV4_int8_convrot.safetensors"


def test_agent_converts_next_to_the_source_and_never_changes_it(tmp_path):
    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    source = models / "diffusion_models" / "m_bf16.safetensors"
    source.write_bytes(b"weights")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    job = {"path": str(source), "sha256": hashlib.sha256(b"weights").hexdigest()}

    def ok(cmd, **kwargs):
        Path(cmd[-2]).write_bytes(b"int8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def broken(cmd, **kwargs):
        Path(cmd[-2]).write_bytes(b"half")
        return subprocess.CompletedProcess(cmd, 1, "", "Traceback...\nmodel.0.weight would lose too much precision")

    with pytest.raises(ValueError, match="precision"):
        convert(job, [str(models)], python, run=broken)
    assert not list((models / "diffusion_models").glob("*.partial"))
    with pytest.raises(ValueError, match="changed"):
        convert({**job, "sha256": "0" * 64}, [str(models)], python, run=ok)
    with pytest.raises(ValueError, match="Python"):
        convert(job, [str(models)], None, run=ok)
    result = convert(job, [str(models)], python, run=ok)
    assert result["path"] == str(source.with_name("m_int8_convrot.safetensors"))
    assert result["sha256"] == hashlib.sha256(b"int8").hexdigest() and source.read_bytes() == b"weights"
    with pytest.raises(ValueError, match="already exists"):
        convert(job, [str(models)], python, run=ok)


def test_conversion_inherits_a_hugging_face_link(db):
    db.execute("INSERT INTO models VALUES ('pc:comfyui','C:/m/k.safetensors','k.safetensors','diffusion',24,1,NULL,?)", (SRC,))
    db.execute("""INSERT INTO selected_matches (device,path,repository,remote_path,sha256,local_size,local_modified_ns,
        selected_at,latest_sha,base_model) VALUES ('pc:comfyui','C:/m/k.safetensors','krea/krea-2','k.safetensors',?,24,1,1,?,'Krea 2')""",
               (SRC, SRC))
    job = converts.request(db, "pc:comfyui", "C:/m/k.safetensors")
    converts.claim(db, "pc")
    converts.finish(db, job["id"], "pc", None, {"path": "C:/m/k_int8_convrot.safetensors", "sha256": NEW,
                                                "size_bytes": 12, "modified_ns": 5})
    # What the next scan reports for the new file.
    db.execute("INSERT INTO models VALUES ('pc:comfyui','C:/m/k_int8_convrot.safetensors','k_int8_convrot.safetensors','diffusion',12,5,NULL,?)", (NEW,))
    record = {"device": "pc:comfyui", "path": "C:/m/k_int8_convrot.safetensors", "name": "k", "kind": "diffusion",
              "sha256": NEW, "size_bytes": 12, "modified_ns": 5}
    source = annotate(db, [record])[0]["source"]
    assert (source["provider"], source["model_name"], source["base_model"]) == ("huggingface", "krea/krea-2", "Krea 2")


def test_conversion_cut_off_by_a_restart_can_be_retried(db):
    path = "C:/m/diffusion_models/big_bf16.safetensors"
    db.execute("INSERT INTO models VALUES ('pc:comfyui',?,'big_bf16.safetensors','diffusion',24,1,NULL,?)", (path, SRC))
    job = converts.request(db, "pc:comfyui", path)
    assert converts.claim(db, "pc")["id"] == job["id"]
    assert converts.claim(db, "pc") is None and converts.get(db, job["id"])["state"] == "running"
    db.execute("UPDATE convert_jobs SET updated_at=0")
    assert converts.claim(db, "pc") is None
    assert converts.get(db, job["id"])["state"] == "failed"
    assert converts.request(db, "pc:comfyui", path)["state"] == "queued"
