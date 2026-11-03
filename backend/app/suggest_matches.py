"""Automatic online match suggestions for local unmatched models (Civitai + Hugging Face)."""
import json
import re
import time
from urllib.parse import quote, urlencode
from fastapi import HTTPException
from .hf_repository import list_model_files, validate_repo
from .sources import civitai_check, fetch

EXT_CLEAN = re.compile(r"\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx)$", re.IGNORECASE)
SUFFIX_CLEAN = re.compile(
    r"[-_](?:v\d+(?:\.\d+)?|fp8|bf16|fp16|fp32|pruned|comfyui|comfy|diffusers)(?=[-_]|$)",
    re.IGNORECASE,
)

def clean_search_query(filename: str, meta_title: str | None = None) -> str:
    """Derive a clean search query from a model file name or embedded metadata title."""
    if meta_title and len(meta_title.strip()) >= 3:
        return meta_title.strip()[:100]
    base = EXT_CLEAN.sub("", filename or "")
    base = SUFFIX_CLEAN.sub("", base)
    cleaned = re.sub(r"[-_.]+", " ", base).strip()
    return cleaned[:100] if len(cleaned) >= 3 else filename[:100]

def suggest_matches(db, device: str, path: str, get=fetch) -> dict:
    """Find candidate matches across Civitai and Hugging Face for a local model file."""
    row = db.execute(
        "SELECT name, size_bytes, sha256, weights_sha256, meta_title FROM models WHERE device=? AND path=?",
        (device, path),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Model not found in inventory")

    name, size, sha, weights, title = row
    query = clean_search_query(name, title)

    # 1. Civitai suggestions
    civitai_results = []
    if len(query) >= 3:
        try:
            cv = civitai_check(query, sha, weights, size, get=get)
            for m in cv.get("models") or []:
                for f in m.get("files") or []:
                    civitai_results.append({
                        "provider": "civitai",
                        "model_id": m["id"],
                        "model_name": m["name"],
                        "version_id": f.get("version_id"),
                        "version_name": f.get("version_name"),
                        "file_id": f.get("file_id"),
                        "file_name": f.get("file_name"),
                        "verdict": f.get("verdict"),
                        "base_model": f.get("base_model"),
                        "url": m["url"],
                    })
        except Exception:
            pass

    # 2. Hugging Face suggestions
    hf_results = []
    if len(query) >= 3:
        try:
            params = {"search": query, "limit": 4}
            body = get("https://huggingface.co/api/models?" + urlencode(params))
            repos = json.loads(body) if body else []
            for r in repos:
                repo_id = str(r.get("id") or "")
                if not repo_id or "/" not in repo_id:
                    continue
                try:
                    listing = list_model_files(repo_id)
                except Exception:
                    continue
                for f in listing.get("files") or []:
                    f_name = f["name"].lower()
                    local_name_lower = name.lower()
                    is_exact_sha = sha and f.get("sha256") and f["sha256"].lower() == sha.lower()
                    is_same_size = size and f.get("size_bytes") and abs(f["size_bytes"] - size) <= 1024
                    is_same_name = f_name == local_name_lower or clean_search_query(f_name) == clean_search_query(local_name_lower)

                    if is_exact_sha:
                        verdict = "exact"
                    elif is_same_size and is_same_name:
                        verdict = "same_weights"
                    elif is_same_name:
                        verdict = "same_name"
                    elif is_same_size:
                        verdict = "same_size"
                    else:
                        continue

                    hf_results.append({
                        "provider": "huggingface",
                        "repository": repo_id,
                        "remote_path": f["path"],
                        "file_name": f["name"],
                        "size_bytes": f.get("size_bytes"),
                        "sha256": f.get("sha256"),
                        "verdict": verdict,
                        "url": f.get("url"),
                    })
        except Exception:
            pass

    VERDICT_ORDER = {"exact": 0, "same_weights": 1, "same_name": 2, "same_size": 3}
    civitai_results.sort(key=lambda x: VERDICT_ORDER.get(x["verdict"], 9))
    hf_results.sort(key=lambda x: VERDICT_ORDER.get(x["verdict"], 9))

    return {
        "device": device,
        "path": path,
        "query": query,
        "civitai": civitai_results[:6],
        "huggingface": hf_results[:6],
    }

def link_huggingface(db, device: str, path: str, repository: str, remote_path: str, now: int | None = None) -> dict:
    """Link a local model file to a specific Hugging Face repository file."""
    now = now or int(time.time())
    repo = validate_repo(repository)
    row = db.execute("SELECT size_bytes, modified_ns, sha256 FROM models WHERE device=? AND path=?", (device, path)).fetchone()
    if not row:
        raise HTTPException(404, "Local model not found in inventory")
    size, modified, sha = row

    listing = list_model_files(repo)
    remote = next((f for f in listing.get("files") or [] if f["path"] == remote_path), None)
    if not remote:
        raise HTTPException(404, "File not found in that Hugging Face repository")

    remote_sha = remote.get("sha256") or sha or ""
    db.execute("""INSERT INTO selected_matches
        (device, path, repository, remote_path, sha256, local_size, local_modified_ns, selected_at, latest_sha, latest_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device, path) DO UPDATE SET
        repository=excluded.repository, remote_path=excluded.remote_path,
        sha256=excluded.sha256, local_size=excluded.local_size,
        local_modified_ns=excluded.local_modified_ns, selected_at=excluded.selected_at,
        latest_sha=excluded.latest_sha, latest_checked_at=excluded.latest_checked_at""",
        (device, path, repo, remote_path, remote_sha.lower(), size, modified, now, remote_sha.lower(), now))

    return {
        "ok": True,
        "device": device,
        "path": path,
        "repository": repo,
        "remote_path": remote_path,
        "sha256": remote_sha.lower(),
    }
