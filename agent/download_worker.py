"""Download user-approved model updates next to the old file, and delete the old file only when asked."""
import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from hash_worker import allowed_file
from hash_cache import sha256_file

ALLOWED_HOSTS = {"civitai.com", "civitai.red", "huggingface.co", "modelscope.cn", "www.modelscope.cn"}
SAFE_NAME = re.compile(r"^[\w .()+,-]{1,200}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx)$")
CHUNK = 8 * 1024 * 1024
HEADROOM = 2 * 1024**3
PROGRESS_SECONDS = 15
# Subfolders of the models root that a new download (one with no old file) may go into.
NEW_FOLDERS = {"checkpoints", "loras", "embeddings", "vae", "controlnet", "upscale_models",
               "text_encoders", "diffusion_models"}

def auth_headers(host):
    # Keys live in the Windows user environment, never in the NAS database or config files.
    name = "CIVITAI_API_TOKEN" if host.startswith("civitai.") else "HF_TOKEN" if host == "huggingface.co" else None
    token = name and os.environ.get(name)
    return {"Authorization": "Bearer " + token} if token else {}

def explain(status, host):
    if status in (401, 403):
        if "modelscope" in host:
            return f"{host} refused the download (HTTP {status}); the model may need you to accept its license on the site"
        name = "CIVITAI_API_TOKEN" if host.startswith("civitai.") else "HF_TOKEN"
        return f"{host} refused the download (HTTP {status}); set {name} in your Windows user environment"
    return f"{host} returned HTTP {status}"

def download_file(session, job, roots, report_progress):
    """Return (sha256, final_path). Raises ValueError with a readable reason."""
    if job.get("target_folder"):
        if job["target_folder"] not in NEW_FOLDERS or not roots:
            raise ValueError("Download folder is not allowed")
        directory = Path(roots[0]).resolve(strict=True) / job["target_folder"]
        directory.mkdir(exist_ok=True)
    else:
        directory = allowed_file(job["path"], roots).parent
    name = job["file_name"]
    if not SAFE_NAME.fullmatch(name or "") or Path(name).name != name:
        raise ValueError("Unsafe file name")
    url = urlsplit(job["url"] or "")
    if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS or url.port:
        raise ValueError("Download host is not allowed")
    target = directory / name
    partial = target.with_name(name + ".partial")
    if target.exists():
        raise ValueError(f"{name} already exists in {directory.name}")
    needed = (job.get("size_bytes") or 0) + HEADROOM
    if shutil.disk_usage(directory).free < needed:
        raise ValueError("Not enough free disk space")
    digest, done, last = hashlib.sha256(), 0, time.monotonic()
    # requests drops the Authorization header when a redirect leaves the original host (CDN links).
    with session.get(job["url"], headers=auth_headers(url.hostname), stream=True, timeout=(30, 300)) as response:
        if response.status_code != 200:
            raise ValueError(explain(response.status_code, url.hostname))
        if "text/html" in response.headers.get("Content-Type", ""):
            raise ValueError(explain(401, url.hostname))  # a login page instead of the file
        try:
            with open(partial, "xb") as out:
                for chunk in response.iter_content(CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > PROGRESS_SECONDS:
                        report_progress(done)
                        last = time.monotonic()
            sha = digest.hexdigest()
            if job.get("expected_sha") and sha != job["expected_sha"]:
                raise ValueError("Downloaded file hash did not match the provider")
            if target.exists():
                raise ValueError(f"{name} appeared while downloading")
            os.replace(partial, target)
        finally:
            if partial.exists():
                partial.unlink()
    return sha, str(target)

def pull_ollama(session, ollama_url, name):
    response = session.post(ollama_url.rstrip("/") + "/api/pull", json={"model": name, "stream": False},
                            timeout=(30, 6 * 3600))
    if response.status_code != 200:
        raise ValueError(f"Ollama pull failed (HTTP {response.status_code})")
    return None, "ollama://" + name

def process_one(session, server, token, agent, roots, ollama_url):
    headers = {"X-ModelHarbor-Token": token}
    base = server.rstrip("/") + "/api/downloads/"
    res = session.get(base + "next", params={"agent": agent}, headers=headers, timeout=20)
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    def report_progress(done):
        session.post(base + job["id"] + "/progress", headers=headers,
                     json={"agent": agent, "bytes_done": done}, timeout=20)
    result = {"agent": agent, "sha256": None, "error": None, "result_path": None}
    try:
        if job["provider"] == "ollama":
            result["sha256"], result["result_path"] = pull_ollama(session, ollama_url, job["file_name"])
        else:
            result["sha256"], result["result_path"] = download_file(session, job, roots, report_progress)
    except (OSError, ValueError, requests.RequestException) as exc:
        result["error"] = str(exc)[:300]
    session.post(base + job["id"] + "/result", headers=headers, json=result, timeout=30).raise_for_status()
    return True

def delete_old(job, roots, hasher=sha256_file):
    """Permanently delete the old version, but only if the verified new file is still intact
    and the old file is exactly the one that was inventoried."""
    old, new = allowed_file(job["path"], roots), allowed_file(job["new_path"], roots)
    if old.resolve() == new.resolve():
        raise ValueError("Old and new paths are the same file")
    if hasher(new) != job["new_sha"]:
        raise ValueError("The new file no longer matches its verified hash; nothing was deleted")
    if hasher(old) != job["old_sha"]:
        raise ValueError("The old file changed since it was inventoried; nothing was deleted")
    os.remove(old)

def process_cleanup(session, server, token, agent, roots):
    headers = {"X-ModelHarbor-Token": token}
    base = server.rstrip("/") + "/api/downloads/"
    res = session.get(base + "cleanup-next", params={"agent": agent}, headers=headers, timeout=20)
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    error = None
    try:
        delete_old(job, roots)
    except (OSError, ValueError) as exc:
        error = str(exc)[:300]
    session.post(base + job["id"] + "/cleanup-result", headers=headers,
                 json={"agent": agent, "error": error}, timeout=30).raise_for_status()
    return True
