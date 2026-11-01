from agent.hash_cache import fill_hashes, load

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
