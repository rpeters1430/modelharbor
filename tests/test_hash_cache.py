import hashlib
import json
import struct
from agent.hash_cache import file_hashes, fill_hashes, load

def record(path, file_id=None, size=5, mtime=1):
    return {"path": str(path), "file_id": file_id, "size_bytes": size, "modified_ns": mtime}

def test_hashes_once_and_shares_hard_links(tmp_path):
    cache = tmp_path / "hashes.json"
    calls = []
    def hasher(path):
        calls.append(path)
        return "f" * 64
    fill_hashes([record("a", "1:9"), record("SDXL/a", "1:9"), record("b")], cache, hasher)
    assert calls == ["a", "b"]  # hard link hashed once
    rows = [record("a", "1:9"), record("b")]
    fill_hashes(rows, cache, hasher)
    assert calls == ["a", "b"] and all(r["sha256"] == "f" * 64 for r in rows)

def test_rehashes_changed_files_and_forgets_removed(tmp_path):
    cache = tmp_path / "hashes.json"
    calls = []
    def hasher(path):
        calls.append(path)
        return "e" * 64
    fill_hashes([record("a"), record("b")], cache, hasher)
    fill_hashes([record("a", mtime=2)], cache, hasher)
    assert calls == ["a", "b", "a"]
    assert set(load(cache)) == {"a"}

def test_unreadable_file_is_skipped(tmp_path):
    def hasher(path):
        raise PermissionError("locked")
    rows = [record("a")]
    fill_hashes(rows, tmp_path / "hashes.json", hasher)
    assert "sha256" not in rows[0]

def test_weights_hash_ignores_the_safetensors_header(tmp_path):
    data = b"\x01" * 100
    def write(name, meta):
        header = json.dumps({"__metadata__": meta}).encode()
        path = tmp_path / name
        path.write_bytes(struct.pack("<Q", len(header)) + header + data)
        return path
    a, b = file_hashes(write("a.safetensors", {"x": "1"})), file_hashes(write("b.safetensors", {"x": "edited"}))
    assert a[0] != b[0] and a[1] == b[1] == hashlib.sha256(data).hexdigest()
    other = tmp_path / "c.ckpt"
    other.write_bytes(data)
    assert file_hashes(other) == (hashlib.sha256(data).hexdigest(), None)

def test_old_cache_entries_get_the_weights_hash_once(tmp_path):
    cache = tmp_path / "hashes.json"
    cache.write_text(json.dumps({"m.safetensors": [5, 1, "a" * 64], "m.ckpt": [5, 1, "b" * 64]}))
    calls = []
    def hasher(path):
        calls.append(path)
        return "a" * 64, "c" * 64
    rows = [record("m.safetensors"), record("m.ckpt")]
    fill_hashes(rows, cache, hasher)
    fill_hashes([record("m.safetensors"), record("m.ckpt")], cache, hasher)
    assert calls == ["m.safetensors"] and rows[0]["weights_sha256"] == "c" * 64 and "weights_sha256" not in rows[1]
