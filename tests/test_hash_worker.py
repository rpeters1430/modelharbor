"""Read-only SHA-256 worker tests, using tiny temporary files only."""
import hashlib
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"agent"))
from hash_worker import allowed_file, hash_requested_model
import pytest

def test_hashes_only_approved_path(tmp_path):
    root=tmp_path/"models"
    root.mkdir()
    file=root/"test.safetensors"
    file.write_bytes(b"abc")
    stat=file.stat()
    job={"path":str(file),"size_bytes":stat.st_size,"modified_ns":stat.st_mtime_ns}
    assert hash_requested_model(job,[str(root)])==hashlib.sha256(b"abc").hexdigest()

def test_rejects_files_outside_model_roots(tmp_path):
    root=tmp_path/"models"
    root.mkdir()
    other=tmp_path/"other.safetensors"
    other.write_bytes(b"secret")
    with pytest.raises(ValueError):
        allowed_file(str(other),[str(root)])

def test_rejects_stale_file_metadata(tmp_path):
    root=tmp_path/"models"
    root.mkdir()
    file=root/"test.safetensors"
    file.write_bytes(b"changed")
    with pytest.raises(ValueError,match="changed"):
        hash_requested_model({"path":str(file),"size_bytes":1,"modified_ns":0},[str(root)])

def test_symlink_is_rejected(tmp_path):
    root=tmp_path/"models"
    root.mkdir()
    outside=tmp_path/"outside"
    outside.write_bytes(b"data")
    symlink=root/"linked.safetensors"
    try:
        symlink.symlink_to(outside)
    except (OSError,NotImplementedError):
        pytest.skip("Symlinks unavailable on this platform")
    with pytest.raises(ValueError):
        allowed_file(str(symlink),[str(root)])
