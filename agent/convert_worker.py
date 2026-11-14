"""Run dashboard-approved int8_convrot conversions with ComfyUI's own Python. The source file is never changed."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from hash_cache import sha256_file
from hash_worker import allowed_file

SCRIPT = Path(__file__).with_name("convert_int8.py")
HEADROOM = 2 * 1024**3
TIMEOUT = 3 * 3600
PRECISION = re.compile(r"(?<![a-z0-9])(bf16|fp16|fp32|f16|f32)(?![a-z0-9])", re.I)
# Keep ComfyUI responsive while a conversion runs: below-normal priority on Windows, `nice` on Linux.
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
NICE = [] if os.name == "nt" or not shutil.which("nice") else ["nice", "-n", "10"]


def find_python(cfg, roots):
    """ComfyUI's Python, which has torch, safetensors and comfy_kitchen. `convert_python` in the config wins."""
    if cfg.get("convert_python"):
        return Path(cfg["convert_python"])
    candidates = []
    for root in roots:
        comfy = Path(root).parent
        candidates += [comfy / ".venv/Scripts/python.exe", comfy / "venv/Scripts/python.exe",
                       comfy.parent / "python_embeded/python.exe",
                       comfy / ".venv/bin/python", comfy / "venv/bin/python"]  # a Linux git clone
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    # ComfyUI Desktop keeps models in a shared folder, away from its install.
    candidates += sorted(local.glob("Comfy-Desktop/ComfyUI-Installs/*/ComfyUI/.venv/Scripts/python.exe"))
    candidates.append(Path.home() / "Documents/ComfyUI/.venv/Scripts/python.exe")
    return next((p for p in candidates if p.is_file()), None)


def target_name(name):
    """model_bf16.safetensors -> model_int8_convrot.safetensors; model.safetensors -> model_int8_convrot.safetensors."""
    stem = name[:-len(".safetensors")]
    new, count = PRECISION.subn("int8_convrot", stem, count=1)
    return (new if count else stem + "_int8_convrot") + ".safetensors"


def convert(job, roots, python, hasher=sha256_file, run=subprocess.run):
    """Return the new file's {path, sha256, size_bytes, modified_ns}. Raises ValueError with a readable reason."""
    if not python or not Path(python).is_file():
        raise ValueError("ComfyUI's Python was not found; set convert_python in agent.json to its Python")
    source = allowed_file(job["path"], roots)
    if not source.name.lower().endswith(".safetensors"):
        raise ValueError("Only .safetensors files can be converted")
    if hasher(source) != job["sha256"]:
        raise ValueError("The file changed since it was inventoried; nothing was converted")
    target = source.with_name(target_name(source.name))
    if target.exists():
        raise ValueError(f"{target.name} already exists")
    if shutil.disk_usage(source.parent).free < source.stat().st_size * 0.6 + HEADROOM:
        raise ValueError("Not enough free disk space for the converted copy")
    partial = target.with_name(target.name + ".partial")
    try:
        try:
            done = run([*NICE, str(python), str(SCRIPT), str(source), str(partial), job["sha256"]], capture_output=True,
                       text=True, timeout=TIMEOUT, creationflags=FLAGS)
        except subprocess.TimeoutExpired:
            raise ValueError("The conversion took longer than three hours and was stopped") from None
        if done.returncode:
            lines = (done.stderr or "").strip().splitlines()
            raise ValueError((lines[-1] if lines else "The conversion failed")[:300])
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    stat = target.stat()
    # Same spelling as the inventory path, so the dashboard links the two.
    path = job["path"][:len(job["path"]) - len(source.name)] + target.name
    return {"path": path, "sha256": hasher(target), "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns}


def process_convert(session, server, token, agent, roots, cfg):
    headers = {"X-ModelHarbor-Token": token}
    base = server.rstrip("/") + "/api/converts/"
    res = session.get(base + "next", params={"agent": agent}, headers=headers, timeout=20)
    res.raise_for_status()
    job = res.json().get("job")
    if not job:
        return False
    body = {"agent": agent, "error": None}
    try:
        body.update(convert(job, roots, find_python(cfg, roots)))
    except (OSError, ValueError) as exc:
        body["error"] = str(exc)[:300]
    session.post(base + job["id"] + "/result", headers=headers, json=body, timeout=30).raise_for_status()
    return True

