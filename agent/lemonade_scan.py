"""Read-only Lemonade Server inventory collection and ModelHarbor upload."""
import argparse
import hashlib
import os
import re
import socket
from urllib.parse import urlsplit
import requests

DEFAULT_URL = "http://127.0.0.1:13305"  # Lemonade 9+; older releases listened on :8000

def auth_headers():
    """Lemonade started with an API key wants it as a bearer token. Like other keys, it lives in
    the Windows user environment (LEMONADE_API_KEY), never in config files or on the NAS."""
    key = os.environ.get("LEMONADE_API_KEY")
    return {"Authorization": "Bearer " + key} if key else {}

def raise_for_status(response):
    if response.status_code in (401, 403):
        raise ValueError(f"Lemonade refused the request (HTTP {response.status_code}); "
                         "set LEMONADE_API_KEY in the Windows user environment to the server's API key")
    response.raise_for_status()

def _vram(gpu):
    value = float(gpu.get("vram_gb") or gpu.get("vram") or 0)
    return round(value if value > 1e6 else value * 2**30)  # some Lemonade builds report bytes under "vram_gb"

def system_info(session, base_url):
    """The Lemonade machine's GPU, RAM and free model storage, for sizing LLM downloads.
    Lemonade may run on another PC than the agent, so the agent's own hardware does not apply."""
    response = session.get(base_url.rstrip("/") + "/api/v1/system-info", headers=auth_headers(), timeout=20)
    raise_for_status(response)
    body = response.json()
    devices = body.get("devices") or {}
    gpus = [(vendor, g) for vendor in ("nvidia", "amd") for g in devices.get(vendor + "_gpu") or []
            if isinstance(g, dict) and g.get("available") and g.get("name")]
    gpus.sort(key=lambda vg: (bool(vg[1].get("integrated")), -_vram(vg[1])))  # discrete, biggest first
    memory = re.match(r"\s*([\d.]+)\s*GB", str(body.get("Physical Memory") or ""))
    storage = body.get("model_storage") or {}
    info = {"host": urlsplit(base_url).hostname,
            "ram_bytes": int(float(memory[1]) * 2**30) if memory else None,
            "free_bytes": int(storage["free_bytes"]) if isinstance(storage.get("free_bytes"), (int, float)) else None}
    if gpus:
        vendor, gpu = gpus[0]
        info.update(gpu=str(gpu["name"])[:120], vendor=vendor, vram_bytes=_vram(gpu) or None)
    return info

def collect(session, base_url, device):
    """List the models Lemonade has downloaded (GET /api/v1/models shows only local ones by default)."""
    response = session.get(base_url.rstrip("/") + "/api/v1/models", headers=auth_headers(), timeout=20)
    raise_for_status(response)
    records = []
    for item in response.json().get("data", []):
        checkpoint = str((item.get("checkpoints") or {}).get("main") or item.get("checkpoint") or "")[:1024] or None
        name = item.get("id")
        if not name or item.get("downloaded") is False or item.get("recipe") == "cloud":
            continue
        try:
            size = max(0, int(float(item.get("size") or 0) * 2**30))  # Lemonade reports GiB, 2 decimals
        except (TypeError, ValueError):
            size = 0
        records.append({
            "device": device, "path": "lemonade://" + name,
            "name": name, "kind": "lemonade/" + str(item.get("recipe") or "unknown")[:50],
            "size_bytes": size, "modified_ns": 0,
            "sha256": None,  # Lemonade publishes no file digest
            # "owner/repo:variant" when Lemonade pulled it from Hugging Face, else the local .gguf path.
            "checkpoint": checkpoint,
            "update_available": bool(item.get("update_available")),
            # Lemonade lists one file under several names ("gemma4-12b", "extra.gemma4-12b", "gemma4:12b").
            # The same file id makes the dashboard count it once, like a hard link.
            "file_id": checkpoint and "ckpt:" + hashlib.sha256(checkpoint.casefold().encode()).hexdigest()[:40],
        })
    return records

def main():
    parser = argparse.ArgumentParser(description="Inventory Lemonade Server models without changing them")
    parser.add_argument("--lemonade-url", default=DEFAULT_URL)
    parser.add_argument("--server", default="http://127.0.0.1:8096")
    parser.add_argument("--device", default=socket.gethostname())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # Separate namespace prevents replacing ComfyUI or Ollama inventory on the same host.
    device = args.device + ":lemonade"
    with requests.Session() as session:
        models = collect(session, args.lemonade_url, device)
        print(f"Found {len(models)} Lemonade models")
        if args.dry_run:
            for model in models:
                print(model["name"], model["kind"], model["size_bytes"])
            return
        token = os.environ.get("MODELHARBOR_AGENT_TOKEN")
        if not token:
            parser.error("MODELHARBOR_AGENT_TOKEN is required")
        response = session.post(args.server.rstrip("/") + "/api/inventory",
            headers={"X-ModelHarbor-Token": token},
            json={"device": device, "models": models}, timeout=120)
        response.raise_for_status()
        print(response.json())

if __name__ == "__main__":
    main()
