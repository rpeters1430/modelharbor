"""Read-only Hugging Face model repository file browser.

Only requests the official Hugging Face API. Never downloads model file contents.
"""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, parse_qs
from urllib.request import Request, urlopen
from fastapi import HTTPException

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_EXTENSIONS = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")
MAX_PAGES = 10
MAX_ENTRIES = 9000
CACHE_TTL = 600
_cache = {}

def validate_repo(repo):
    if not _REPO.fullmatch(repo) or ".." in repo or "/." in repo:
        raise HTTPException(422, "Expected an owner/repository name")
    return repo

def _json_page(url):
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "ModelHarbor/0.3 (read-only Hugging Face metadata)",
    })
    try:
        with urlopen(request, timeout=12) as response:
            data = response.read(5_000_001)
            if len(data) > 5_000_000:
                raise HTTPException(502, "Hugging Face response too large")
            body = json.loads(data)
            if not isinstance(body, list):
                raise HTTPException(502, "Unexpected Hugging Face response")
            link = response.headers.get("Link", "")
            return body, _next_cursor(link)
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(404, "Hugging Face repository not found or inaccessible") from exc
        if exc.code in (401, 403):
            raise HTTPException(403, "Repository is restricted or inaccessible") from exc
        if exc.code == 429:
            raise HTTPException(503, "Hugging Face rate limit reached") from exc
        raise HTTPException(502, "Hugging Face request failed") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(502, "Hugging Face temporarily unavailable") from exc

def _next_cursor(link):
    # Never follow a URL from a remote Link header. Extract only its cursor.
    for piece in link.split(","):
        if 'rel="next"' not in piece and "rel=next" not in piece:
            continue
        match = re.search(r"<([^>]+)>", piece)
        if not match:
            continue
        url = urlsplit(match.group(1))
        if url.scheme != "https" or url.hostname != "huggingface.co" or url.port is not None:
            continue
        cursor = parse_qs(url.query).get("cursor", [""])[0]
        if 0 < len(cursor) <= 2048:
            return cursor
    return None

def list_model_files(repo, query=""):
    repo = validate_repo(repo)
    query = query.strip()
    if len(query) > 120:
        raise HTTPException(422, "Search query too long")
    key = (repo, query.casefold())
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]

    endpoint = "https://huggingface.co/api/models/" + quote(repo, safe="/") + "/tree/main"
    entries = []
    cursor = None
    complete = True
    for page in range(MAX_PAGES):
        params = {"recursive": "true"}
        if cursor:
            params["cursor"] = cursor
        body, next_cursor = _json_page(endpoint + "?" + urlencode(params))
        entries.extend(body)
        cursor = next_cursor
        if len(entries) >= MAX_ENTRIES:
            complete = not bool(cursor)
            break
        if not cursor:
            break
    else:
        complete = not bool(cursor)

    needle = query.casefold()
    files = []
    folders = set()
    for item in entries:
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if item.get("type") == "directory":
            folders.add(path)
            continue
        if item.get("type") != "file" or not path.lower().endswith(_EXTENSIONS):
            continue
        if needle and needle not in path.casefold() and needle not in path.rsplit("/", 1)[-1].casefold().replace("_", " "):
            continue
        lfs = item.get("lfs") or {}
        digest = lfs.get("sha256") if isinstance(lfs, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            digest = None
        size = item.get("size")
        files.append({
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "size_bytes": size if isinstance(size, int) and size >= 0 else None,
            "sha256": digest,
            "url": "https://huggingface.co/" + quote(repo, safe="/") + "/blob/main/" + quote(path, safe="/"),
        })
    files.sort(key=lambda x: x["path"].casefold())
    result = {
        "repository": repo,
        "url": "https://huggingface.co/" + quote(repo, safe="/") + "/tree/main",
        "query": query,
        "files": files,
        "folders": sorted(folders),
        "complete": complete,
        "scanned_entries": len(entries),
        "note": None if complete else "Repository too large to list completely; results may omit files.",
    }
    if len(_cache) > 100:
        _cache.clear()
    _cache[key] = (time.monotonic(), result)
    return result
