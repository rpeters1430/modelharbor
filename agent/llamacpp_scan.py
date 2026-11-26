"""Read-only scan of a llama.cpp models folder (llama-server --models-dir), plus its restart hook.

llama-server's router mode offers every .gguf file in that folder under its file name, and only
reads the folder when it starts, so the agent restarts it after adding or deleting a model."""
import logging
import subprocess
from pathlib import Path

LOG = logging.getLogger("modelharbor.llamacpp")
DEFAULT_RESTART = ["docker", "restart", "llama-cpp"]


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
