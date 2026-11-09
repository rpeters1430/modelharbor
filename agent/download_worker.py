"""Download user-approved model updates next to the old file, and delete the old file only when asked."""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from hash_worker import allowed_file
from hash_cache import sha256_file
from lemonade_scan import DEFAULT_URL as LEMONADE_URL, auth_headers as lemonade_headers

ALLOWED_HOSTS = {"civitai.com", "civitai.red", "huggingface.co", "modelscope.cn", "www.modelscope.cn"}
SAFE_NAME = re.compile(r"^[\w .()+,-]{1,200}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx)$")
FAMILY = re.compile(r"^[A-Za-z0-9]{1,20}$")
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
        # A fixed ComfyUI folder, optionally with one family subfolder ("loras/SDXL").
        parent, _, family = job["target_folder"].partition("/")
        if parent not in NEW_FOLDERS or family and not FAMILY.fullmatch(family) or not roots:
            raise ValueError("Download folder is not allowed")
        directory = Path(roots[0]).resolve(strict=True) / parent / family
        directory.mkdir(parents=True, exist_ok=True)
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
        # Ours by name: left behind by a crash or restart mid-download, so start it over.
        partial.unlink(missing_ok=True)
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

def pull_ollama(session, ollama_url, name, report_progress=lambda done: None):
    """Stream Ollama's pull so progress reaches the NAS. Without it, a pull over 30 minutes looked
    stalled, was handed out again and ran twice."""
    with session.post(ollama_url.rstrip("/") + "/api/pull", json={"model": name, "stream": True},
                      stream=True, timeout=(30, 3600)) as response:
        if response.status_code != 200:
            raise ValueError(f"Ollama pull failed (HTTP {response.status_code})")
        last, status = 0.0, None
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ValueError("Ollama returned an invalid pull response") from exc
            if event.get("error"):
                raise ValueError("Ollama pull failed: " + str(event["error"])[:250])
            status = event.get("status")
            if event.get("completed") and time.monotonic() - last > PROGRESS_SECONDS:
                report_progress(int(event["completed"]))
                last = time.monotonic()
        if status != "success":
            raise ValueError("Ollama closed the connection before the pull finished")
    return None, "ollama://" + name

LEMONADE_NAME = re.compile(r"^user\.[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
LEMONADE_CHECKPOINT = re.compile(r"^[\w.-]{1,96}/[\w.-]{1,96}:[\w .()+,-]{1,200}\.gguf$")

def _lemonade_error(response):
    if response.status_code in (401, 403):
        return f"HTTP {response.status_code}; set LEMONADE_API_KEY in the Windows user environment to the server's API key"
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return f"HTTP {response.status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    return str(error or body.get("message") or f"HTTP {response.status_code}")[:250]

def _lemonade_stream(response, report_progress):
    """Follow /api/v1/pull's Server-Sent Events, reporting bytes so the NAS knows the pull is alive."""
    event, finished, current, total, last = None, 0, None, 0, 0.0
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        if event == "error":
            raise ValueError("Lemonade pull failed: " + str(data.get("error") or data.get("message") or "unknown error")[:250])
        if event == "complete":
            return
        if event == "progress":
            if data.get("file_index") != current:  # moved on to the next file of a multi-file pull
                finished += total
                current = data.get("file_index")
            total = int(data.get("bytes_total") or 0)
            if time.monotonic() - last > PROGRESS_SECONDS:  # Lemonade sends many events a second
                report_progress(finished + int(data.get("bytes_downloaded") or 0))
                last = time.monotonic()
    raise ValueError("Lemonade closed the connection before the pull finished")

def pull_lemonade(session, lemonade_url, job, report_progress, load=True):
    """Ask the PC's Lemonade Server to download a Hugging Face GGUF, then load it."""
    name, checkpoint = job["file_name"], job.get("url") or ""
    if not LEMONADE_NAME.fullmatch(name or "") or not LEMONADE_CHECKPOINT.fullmatch(checkpoint):
        raise ValueError("Unsafe Lemonade model name or checkpoint")
    base = lemonade_url.rstrip("/") + "/api/v1/"
    try:
        free = (session.get(base + "system-info", headers=lemonade_headers(), timeout=20).json()
                .get("model_storage") or {}).get("free_bytes")
    except (requests.RequestException, ValueError, AttributeError):
        free = None  # older Lemonade: let the pull itself fail if the disk is full
    if isinstance(free, (int, float)) and job.get("size_bytes") and free < job["size_bytes"] + HEADROOM:
        raise ValueError(f"Not enough free space on the Lemonade PC ({free / 2**30:.0f} GB free)")
    body = {"model_name": name, "checkpoint": checkpoint, "recipe": "llamacpp", "stream": True}
    headers = lemonade_headers()
    with session.post(base + "pull", json=body, headers=headers, stream=True, timeout=(30, 3600)) as response:
        if response.status_code != 200:
            raise ValueError("Lemonade pull failed: " + _lemonade_error(response))
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            _lemonade_stream(response, report_progress)
        else:  # a server that ignores "stream" answers once with {"status": ...}
            try:
                payload = response.json()
            except ValueError as exc:
                raise ValueError("Lemonade returned an invalid pull response") from exc
            if not isinstance(payload, dict) or payload.get("status") == "error" or payload.get("error"):
                raise ValueError("Lemonade pull failed: " + _lemonade_error(response))
    if load:
        response = session.post(base + "load", json={"model_name": name}, headers=headers, timeout=(30, 900))
        if response.status_code != 200:
            raise ValueError("Downloaded into Lemonade, but loading failed: " + _lemonade_error(response))
    return None, "lemonade://" + name

def process_one(session, server, token, agent, roots, ollama_url, lemonade_url=LEMONADE_URL, lemonade_load=True):
    headers = {"X-ModelHarbor-Token": token}
    base = server.rstrip("/") + "/api/downloads/"
    res = session.get(base + "next", params={"agent": agent}, headers=headers, timeout=20)
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    def report_progress(done):
        # Best effort: a NAS hiccup must not abort a download that is going fine.
        try:
            session.post(base + job["id"] + "/progress", headers=headers,
                         json={"agent": agent, "bytes_done": done}, timeout=20)
        except requests.RequestException:
            pass
    result = {"agent": agent, "sha256": None, "error": None, "result_path": None}
    try:
        if job["provider"] == "ollama":
            result["sha256"], result["result_path"] = pull_ollama(session, ollama_url, job["file_name"], report_progress)
        elif job["provider"] == "lemonade":
            result["sha256"], result["result_path"] = pull_lemonade(
                session, lemonade_url, job, report_progress, lemonade_load)
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

# Files ComfyUI managers keep next to a model (previews, Civitai info); removed with it.
SIDECARS = (".metadata.json", ".civitai.info", ".preview.png", ".preview.jpg", ".preview.jpeg", ".preview.webp",
            ".png", ".jpg", ".jpeg", ".webp")

def delete_files(job, roots, hasher=sha256_file):
    """Permanently delete a model file and its hard links, only if it is exactly the inventoried file."""
    files = [allowed_file(p, roots) for p in job["paths"]]
    if not files:
        raise ValueError("Nothing to delete")
    ids = {(s.st_dev, s.st_ino) for s in (f.stat() for f in files)}
    if len(ids) != 1:
        raise ValueError("These paths are no longer the same file; nothing was deleted")
    if hasher(files[0]) != job["sha256"]:
        raise ValueError("The file changed since it was inventoried; nothing was deleted")
    for f in files:
        os.remove(f)
        for suffix in SIDECARS:
            side = f.with_name(f.stem + suffix)
            if side.is_file() and not side.is_symlink():
                side.unlink()

def process_delete(session, server, token, agent, roots):
    headers = {"X-ModelHarbor-Token": token}
    base = server.rstrip("/") + "/api/deletes/"
    res = session.get(base + "next", params={"agent": agent}, headers=headers, timeout=20)
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    error = None
    try:
        delete_files(job, roots)
    except (OSError, ValueError) as exc:
        error = str(exc)[:300]
    session.post(base + job["id"] + "/result", headers=headers,
                 json={"agent": agent, "error": error}, timeout=30).raise_for_status()
    return True

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
