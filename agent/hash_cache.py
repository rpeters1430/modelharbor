"""Persistent SHA-256 cache: each model file is read once until its size or mtime changes."""
import hashlib
import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger("modelharbor")
CHUNK = 8 * 1024 * 1024

def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save(path, cache):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(cache), encoding="utf-8")
    os.replace(partial, path)

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()

def fill_hashes(records, cache_path, hasher=sha256_file, progress=None):
    """Add "sha256" to each record. Hard links share a file_id and are hashed once.

    progress(done, total, done_bytes, total_bytes, path) is called before each file is hashed."""
    cache = load(cache_path)
    key = lambda r: r.get("file_id") or r["path"]
    stamp = lambda r: [r["size_bytes"], r["modified_ns"]]
    pending = {}
    for record in records:
        entry = cache.get(key(record))
        if not isinstance(entry, list) or entry[:2] != stamp(record):
            pending.setdefault(key(record), record["size_bytes"])
    total, total_bytes, done_bytes = len(pending), sum(pending.values()), 0
    fresh = {}
    hashed = 0
    for record in records:
        k = key(record)
        entry = fresh.get(k) or cache.get(k)
        if not isinstance(entry, list) or entry[:2] != stamp(record):
            if progress:
                progress(hashed, total, done_bytes, total_bytes, record["path"])
            done_bytes += record["size_bytes"]
            try:
                entry = stamp(record) + [hasher(record["path"])]
            except OSError as exc:
                LOG.warning("Could not hash %s: %s", record["path"], exc)
                continue
            hashed += 1
            if hashed % 20 == 0:
                # Keep progress if a long first run is interrupted.
                save(cache_path, {**cache, **fresh, k: entry})
        fresh[k] = entry
        record["sha256"] = entry[2]
    if hashed:
        LOG.info("Hashed %d new or changed model files", hashed)
    save(cache_path, fresh)  # drops entries for files that no longer exist
    return records
