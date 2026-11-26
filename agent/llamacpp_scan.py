"""Read-only scan of a llama.cpp models folder (llama-server --models-dir), plus its restart hook.

llama-server's router mode offers every .gguf file in that folder under its file name, and only
reads the folder when it starts, so the agent restarts it after adding or deleting a model."""
import logging
import os
import subprocess
from pathlib import Path

LOG = logging.getLogger("modelharbor.llamacpp")
DEFAULT_RESTART = ["docker", "restart", "llama-cpp"]
DEFAULT_URL = "http://127.0.0.1:8080"
# llama-server's router reports each model as one of these (GET /models, status.value).
STATUSES = {"unloaded", "loading", "loaded", "sleeping", "downloading", "incomplete"}


def auth_headers():
    """llama-server's --api-key, from LLAMACPP_API_KEY in the agent's environment (never agent.json)."""
    key = os.environ.get("LLAMACPP_API_KEY")
    return {"Authorization": "Bearer " + key} if key else {}


def model_id(path):
    """The name llama-server's router gives a GGUF from its models folder: the file name without .gguf."""
    name = Path(path).name
    return name[:-5] if name.lower().endswith(".gguf") else name


def statuses(session, base_url):
    """{model id or file name: "loaded" | "unloaded" | ...} from llama-server's router (GET /models)."""
    response = session.get(base_url.rstrip("/") + "/models", headers=auth_headers(), timeout=10)
    response.raise_for_status()
    found = {}
    for item in (response.json() or {}).get("data") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        value = status.get("value") if status.get("value") in STATUSES else None
        if status.get("failed"):
            value = "failed"
        found[item["id"]] = value
        if isinstance(item.get("path"), str):
            found[Path(item["path"].replace("\\", "/")).name] = value
    return found


def add_statuses(records, session, base_url):
    """Record each model's router status in runtime_meta, so the dashboard can offer Load or Unload.
    Best effort: an unreachable llama-server leaves the list as it is."""
    try:
        found = statuses(session, base_url)
    except Exception as exc:  # noqa: BLE001 - requests or JSON errors; the file list is still right
        LOG.info("llama-server status unavailable at %s: %s", base_url, exc)
        return records
    for record in records:
        mid = model_id(record["path"])
        status = found.get(mid, found.get(record["name"]))
        record["runtime_meta"] = {"model_id": mid, "status": status or "unknown"}
    return records


def scan(root: Path, device: str):
    """GGUF files directly in the folder (subfolders are ignored, like llama-server does)."""
    if not root.is_dir():
        raise ValueError(f"llama.cpp models folder does not exist: {root}")
    found = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() != ".gguf" or path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append({"device": device, "path": str(path), "name": path.name, "kind": "llamacpp/gguf",
                      "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns,
                      "file_id": f"{stat.st_dev}:{stat.st_ino}" if stat.st_ino else None})
    return found


def action(session, base_url, which, mid):
    """Ask llama-server's router to load or unload a model (POST /models/load or /models/unload)."""
    if which not in ("load", "unload") or not mid:
        raise ValueError("Unsupported llama.cpp action")
    response = session.post(base_url.rstrip("/") + "/models/" + which, json={"model": mid},
                            headers=auth_headers(), timeout=(10, 600))  # loading a big model takes a while
    if response.status_code >= 400:
        try:
            detail = response.json().get("error")
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except ValueError:
            detail = None
        raise ValueError(f"llama-server refused to {which} {mid}: {detail or 'HTTP ' + str(response.status_code)}")
    body = response.json() if response.content else {}
    if isinstance(body, dict) and body.get("success") is False:
        raise ValueError(f"llama-server could not {which} {mid}")


def restart(command, run=subprocess.run):
    """Run the configured restart command so llama-server lists the changed folder.
    Returns an error message, or None. An empty command means "don't restart"."""
    if not command:
        return None
    try:
        result = run(list(command), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"restarting llama.cpp failed: {exc}"
    if result.returncode != 0:
        return "restarting llama.cpp failed: " + (result.stderr or result.stdout or "").strip()[:200]
    LOG.info("Restarted llama.cpp so it lists the changed models folder")
    return None
