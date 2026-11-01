"""Browse Civitai models and mark files already on a PC by exact SHA-256."""
import json
import re
from urllib.parse import urlencode
from fastapi import HTTPException
from .sources import CIVITAI_HOSTS, _file_sha, civitai_url, fetch

TYPES = ("Checkpoint", "LORA", "LoCon", "DoRA", "TextualInversion", "VAE", "Controlnet", "Upscaler")
SORTS = ("Most Downloaded", "Highest Rated", "Newest")
# ComfyUI model folder for a downloaded file: the file's own type first, then the model's type.
FILE_FOLDERS = {"VAE": "vae", "Text Encoder": "text_encoders", "UNet": "diffusion_models",
                "Diffusion Model": "diffusion_models", "Upscaler": "upscale_models"}
TYPE_FOLDERS = {"Checkpoint": "checkpoints", "LORA": "loras", "LoCon": "loras", "DoRA": "loras",
                "TextualInversion": "embeddings", "VAE": "vae", "Controlnet": "controlnet", "Upscaler": "upscale_models"}
FOLDERS = sorted(set(FILE_FOLDERS.values()) | set(TYPE_FOLDERS.values()))
CURSOR = re.compile(r"^[A-Za-z0-9|:._-]{1,200}$")
MAX_VERSIONS = 8

def local_shas(db):
    return {r[0] for r in db.execute("SELECT DISTINCT sha256 FROM models WHERE sha256 IS NOT NULL")}

def folder_for(model_type, file_type):
    return FILE_FOLDERS.get(file_type) or TYPE_FOLDERS.get(model_type) or "checkpoints"

def search_civitai(query, model_type, base_model, sort, cursor, shas, get=fetch):
    query = (query or "").strip()
    if len(query) > 120 or len(base_model or "") > 60:
        raise HTTPException(422, "Search text too long")
    if model_type and model_type not in TYPES:
        raise HTTPException(422, "Unsupported model type")
    if sort not in SORTS:
        raise HTTPException(422, "Unsupported sort")
    if cursor and not CURSOR.fullmatch(cursor):
        raise HTTPException(422, "Invalid page cursor")
    params = {"limit": 20, "nsfw": "true", "sort": sort}
    params.update({k: v for k, v in (("query", query), ("types", model_type), ("baseModels", base_model),
                                     ("cursor", cursor)) if v})
    body = get(f"https://{CIVITAI_HOSTS[0]}/api/v1/models?{urlencode(params)}")
    data = json.loads(body) if body else {}
    items = []
    for m in data.get("items") or []:
        if not isinstance(m.get("id"), int):
            continue
        nsfw = bool(m.get("nsfw"))
        versions, installed = [], None
        for v in (m.get("modelVersions") or [])[:MAX_VERSIONS]:
            files = []
            for f in v.get("files") or []:
                sha = _file_sha(f)
                if not isinstance(f.get("id"), int) or not sha:
                    continue
                files.append({"id": f["id"], "name": str(f.get("name") or "")[:200], "type": f.get("type"),
                              "primary": bool(f.get("primary")), "size_bytes": int(float(f.get("sizeKB") or 0) * 1024),
                              "on_pc": sha in shas, "folder": folder_for(m.get("type"), f.get("type"))})
            if any(f["on_pc"] for f in files) and installed is None:
                installed = str(v.get("name") or "")
            versions.append({"id": v.get("id"), "name": str(v.get("name") or "")[:250], "base_model": v.get("baseModel"),
                             "published_at": v.get("publishedAt"), "files": files})
        image = next((i for v in m.get("modelVersions") or [] for i in v.get("images") or []
                      if i.get("type", "image") == "image" and str(i.get("url", "")).startswith("https://")), None)
        items.append({"id": m["id"], "name": str(m.get("name") or "")[:250], "type": m.get("type"), "nsfw": nsfw,
            "url": civitai_url(m["id"], None, nsfw), "creator": str((m.get("creator") or {}).get("username") or "")[:100],
            "downloads": (m.get("stats") or {}).get("downloadCount"),
            # Mature previews are hidden in the dashboard unless the viewer turns them on.
            "image": image and {"url": image["url"], "nsfw_level": int(image.get("nsfwLevel") or 0)},
            "installed_version": installed, "latest_installed": bool(versions) and installed == versions[0]["name"],
            "versions": versions})
    next_cursor = str((data.get("metadata") or {}).get("nextCursor") or "") or None
    return {"items": items, "next_cursor": next_cursor if next_cursor and CURSOR.fullmatch(next_cursor) else None}

def mark_huggingface(listing, shas):
    return {**listing, "files": [{**f, "on_pc": bool(f.get("sha256")) and f["sha256"].lower() in shas}
                                 for f in listing["files"]]}
