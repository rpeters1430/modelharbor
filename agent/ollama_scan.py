"""Read-only Ollama inventory collection and ModelHarbor upload."""
import argparse
import os
import socket
from urllib.parse import urlparse
import requests

def collect(session, base_url, device):
    base = base_url.rstrip("/")
    response = session.get(base + "/api/tags", timeout=20)
    response.raise_for_status()
    records = []
    for item in response.json().get("models", []):
        name = item.get("name") or item.get("model")
        if not name:
            continue
        details = item.get("details") or {}
        digest = item.get("digest") or ""
        # Include digest in path to distinguish revisions and avoid filesystem assumptions.
        records.append({
            "device": device, "path": "ollama://" + name,
            "name": name, "kind": "ollama/" + str(details.get("family") or "unknown")[:50],
            "size_bytes": max(0, int(item.get("size") or 0)),
            "modified_ns": 0,
        })
    return records

def main():
    parser = argparse.ArgumentParser(description="Inventory Ollama models without changing them")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--server", default="http://127.0.0.1:8096")
    parser.add_argument("--device", default=socket.gethostname())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # Separate namespace prevents replacing ComfyUI inventory on the same host.
    device = args.device + ":ollama"
    with requests.Session() as session:
        models = collect(session, args.ollama_url, device)
        print(f"Found {len(models)} Ollama models")
        if args.dry_run:
            for model in models:
                print(model["name"], model["size_bytes"])
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
