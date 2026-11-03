"""Persistent SHA-256 cache: each model file is read once until its size or mtime changes."""
import hashlib
import json
import logging
import os
import struct
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

def file_hashes(path):
    """(SHA-256 of the file, SHA-256 of its tensor data) in one read.

    The second skips a safetensors file's JSON header (Civitai's AutoV3 is its first 12 characters), so a copy
    whose embedded metadata was edited or stripped still matches. None for other formats."""
    full, weights, skip = hashlib.sha256(), None, 0
    with open(path, "rb") as stream:
        if str(path).lower().endswith(".safetensors"):
            head = stream.read(8)
            full.update(head)
            if len(head) == 8 and (size := struct.unpack("<Q", head)[0]) < 100_000_000:
                weights, skip = hashlib.sha256(), size
        while chunk := stream.read(CHUNK):
            full.update(chunk)
            if weights is not None:
                cut = min(skip, len(chunk))
                skip -= cut
                weights.update(memoryview(chunk)[cut:])
    return full.hexdigest(), weights and weights.hexdigest()

def extract_safetensors_metadata(path):
    """Safely read embedded __metadata__ from a safetensors file without reading tensor weights."""
    if not str(path).lower().endswith(".safetensors"):
        return {}
    try:
        with open(path, "rb") as stream:
            head = stream.read(8)
            if len(head) < 8:
                return {}
            size = struct.unpack("<Q", head)[0]
            if not (2 < size < 15_000_000):
                return {}
            header_json = stream.read(size).decode("utf-8", errors="ignore")
            meta = json.loads(header_json).get("__metadata__", {})
            return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}

def parse_embedded_meta(meta):
    """Parse trigger words, base model hint, and title from safetensors __metadata__."""
    if not meta:
        return None, None, None
    trigger_words = []
    if meta.get("modelspec.trigger_phrase"):
        tp = str(meta["modelspec.trigger_phrase"]).strip()
        if tp:
            trigger_words.append(tp)
    tw_raw = meta.get("ss_trained_words")
    if tw_raw:
        try:
            parsed = json.loads(tw_raw) if isinstance(tw_raw, str) else tw_raw
            if isinstance(parsed, list):
                for w in parsed:
                    w = str(w).strip()
                    if w and w not in trigger_words:
                        trigger_words.append(w)
        except Exception:
            pass
    tag_freq = meta.get("ss_tag_frequency")
    if tag_freq and not trigger_words:
        try:
            freq_dict = json.loads(tag_freq) if isinstance(tag_freq, str) else tag_freq
            if isinstance(freq_dict, dict):
                tags = []
                for subdict in freq_dict.values():
                    if isinstance(subdict, dict):
                        for tag, count in sorted(subdict.items(), key=lambda x: x[1], reverse=True)[:5]:
                            tag = str(tag).strip()
                            if tag and tag not in tags and not tag.isdigit():
                                tags.append(tag)
                trigger_words.extend(tags[:4])
        except Exception:
            pass

    base_model = None
    for k in ("modelspec.architecture", "ss_base_model_version", "ss_sd_model_name", "ss_v2"):
        val = meta.get(k)
        if val:
            base_model = str(val)[:100]
            break

    title = meta.get("modelspec.title") or meta.get("ss_output_name")
    if title:
        title = str(title)[:250]

    return (trigger_words or None), base_model, title

def fill_hashes(records, cache_path, hasher=file_hashes, progress=None):
    """Add "sha256" (and "weights_sha256" for safetensors) to each record. Hard links are hashed once.

    Cache entries are [size, mtime, sha256, weights_sha256]; older 3-item entries of safetensors files are
    hashed again once to add the weights hash.

    progress(done, total, done_bytes, total_bytes, path) is called before each file is hashed."""
    cache = load(cache_path)
    key = lambda r: r.get("file_id") or r["path"]
    stamp = lambda r: [r["size_bytes"], r["modified_ns"]]
    stale = lambda r, e: (not isinstance(e, list) or e[:2] != stamp(r)
                          or len(e) < 4 and r["path"].lower().endswith(".safetensors"))
    pending = {}
    for record in records:
        entry = cache.get(key(record))
        if stale(record, entry):
            pending.setdefault(key(record), record["size_bytes"])
    total, total_bytes, done_bytes = len(pending), sum(pending.values()), 0
    fresh = {}
    hashed = 0
    for record in records:
        k = key(record)
        entry = fresh.get(k) or cache.get(k)
        if stale(record, entry):
            if progress:
                progress(hashed, total, done_bytes, total_bytes, record["path"])
            done_bytes += record["size_bytes"]
            try:
                result = hasher(record["path"])
                meta = extract_safetensors_metadata(record["path"])
                tw, base_m, title = parse_embedded_meta(meta)
                entry = stamp(record) + list(result if isinstance(result, tuple) else (result, None)) + [tw, base_m, title]
            except OSError as exc:
                LOG.warning("Could not hash %s: %s", record["path"], exc)
                continue
            hashed += 1
            if hashed % 20 == 0:
                # Keep progress if a long first run is interrupted.
                save(cache_path, {**cache, **fresh, k: entry})
        elif len(entry) < 7 and str(record["path"]).lower().endswith(".safetensors"):
            try:
                meta = extract_safetensors_metadata(record["path"])
                tw, base_m, title = parse_embedded_meta(meta)
                entry = (entry[:4] if len(entry) >= 4 else entry[:3] + [None]) + [tw, base_m, title]
            except Exception:
                pass
        fresh[k] = entry
        record["sha256"] = entry[2]
        if len(entry) > 3 and entry[3]:
            record["weights_sha256"] = entry[3]
        if len(entry) > 4 and entry[4]:
            record["trigger_words"] = entry[4]
        if len(entry) > 5 and entry[5]:
            record["base_model_guess"] = entry[5]
        if len(entry) > 6 and entry[6]:
            record["meta_title"] = entry[6]
    if hashed:
        LOG.info("Hashed %d new or changed model files", hashed)
    save(cache_path, fresh)  # drops entries for files that no longer exist
    return records
