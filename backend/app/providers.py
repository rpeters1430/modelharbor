"""Read-only, explicitly initiated provider discovery. No automatic identity claims."""
import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from fastapi import HTTPException

CACHE = {}
TTL = 900
MAX_CACHE = 200

def get_json(url):
    req = Request(url, headers={"User-Agent": "ModelHarbor/0.2 (model inventory discovery)", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=9) as response:
            data = response.read(1_500_001)
            if len(data) > 1_500_000:
                raise HTTPException(502, "Provider response too large")
            return json.loads(data)
    except HTTPError as exc:
        if exc.code == 429:
            raise HTTPException(503, "Provider rate limit reached") from exc
        raise HTTPException(502, "Provider returned an error") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(502, "Provider temporarily unavailable") from exc

def variants(query):
    cleaned = re.sub(r"\.(safetensors|ckpt|gguf|pt|pth|bin)$", "", query, flags=re.I)
    cleaned = re.sub(r"[_-]+", " ", cleaned).strip()
    shortened = re.sub(r"(?i)\s+v?\d+(?:\.\d+)*$", "", cleaned).strip()
    return list(dict.fromkeys(x for x in (query.strip(), cleaned, shortened) if len(x) >= 3))[:3]

def search(provider, query):
    query = query.strip()
    if len(query) < 3 or len(query) > 120:
        raise HTTPException(422, "Search must contain 3–120 characters")
    if provider not in ("huggingface", "civitai"):
        raise HTTPException(422, "Unsupported provider")
    key = (provider, query.casefold())
    now = time.monotonic()
    cached = CACHE.get(key)
    if cached and now - cached[0] < TTL:
        return cached[1]
    encoded = quote(query, safe="")
    if provider == "huggingface":
        data = []
        for term in variants(query):
            data = get_json("https://huggingface.co/api/models?search=" + quote(term, safe="") + "&limit=12")
            if data:
                break
        results = [{"provider": provider, "id": str(m.get("id", "")),
                    "name": str(m.get("id", "")).split("/")[-1],
                    "url": "https://huggingface.co/" + quote(str(m.get("id", "")), safe="/"),
                    "description": "", "image": None,
                    "downloads": m.get("downloads")} for m in data if m.get("id")]
    else:
        data = get_json("https://civitai.com/api/v1/models?query=" + encoded + "&limit=12")
        results = []
        for m in data.get("items", []):
            if not isinstance(m.get("id"), int):
                continue
            results.append({"provider": provider, "id": str(m["id"]),
                "name": str(m.get("name", ""))[:250],
                "url": "https://civitai.com/models/" + str(m["id"]),
                "description": "", "image": None,
                "downloads": (m.get("stats") or {}).get("downloadCount")})
    if len(CACHE) >= MAX_CACHE:
        CACHE.clear()
    CACHE[key] = (now, results)
    return results
