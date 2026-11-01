"""Download user-approved model updates next to the old file. Never overwrites or deletes."""
import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from hash_worker import allowed_file

ALLOWED_HOSTS = {"civitai.com", "civitai.red", "huggingface.co"}
SAFE_NAME = re.compile(r"^[\w .()+,-]{1,200}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx)$")
CHUNK = 8 * 1024 * 1024
HEADROOM = 2 * 1024**3
PROGRESS_SECONDS = 15

def auth_headers(host):
    # Keys live in the Windows user environment, never in the NAS database or config files.
    token = os.environ.get("CIVITAI_API_TOKEN") if host.startswith("civitai.") else os.environ.get("HF_TOKEN")
    return {"Authorization": "Bearer " + token} if token else {}

def explain(status, host):
    if status in (401, 403):
        name = "CIVITAI_API_TOKEN" if host.startswith("civitai.") else "HF_TOKEN"
        return f"{host} refused the download (HTTP {status}); set {name} in your Windows user environment"
    return f"{host} returned HTTP {status}"

def download_file(session, job, roots, report_progress):
    """Return (sha256, final_path). Raises ValueError with a readable reason."""
    old = allowed_file(job["path"], roots)
    name = job["file_name"]
    if not SAFE_NAME.fullmatch(name or "") or Path(name).name != name:
        raise ValueError("Unsafe file name")
    url = urlsplit(job["url"] or "")
    if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS or url.port:
        raise ValueError("Download host is not allowed")
    target = old.parent / name
    partial = target.with_name(name + ".partial")
    if target.exists():
        raise ValueError(f"{name} already exists next to the old file")
    needed = (job.get("size_bytes") or 0) + HEADROOM
    if shutil.disk_usage(old.parent).free < needed:
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
