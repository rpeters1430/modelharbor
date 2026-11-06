"""Read-only Lemonade Server inventory collection and ModelHarbor upload."""
import argparse
import os
import socket
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

def collect(session, base_url, device):
    """List the models Lemonade has downloaded (GET /api/v1/models shows only local ones by default)."""
    response = session.get(base_url.rstrip("/") + "/api/v1/models", headers=auth_headers(), timeout=20)
    raise_for_status(response)
    records = []
    for item in response.json().get("data", []):
        name = item.get("id")
        if not name or item.get("downloaded") is False or item.get("recipe") == "cloud":
            continue
        try:
            size = max(0, int(float(item.get("size") or 0) * 1e9))  # Lemonade reports GB
        except (TypeError, ValueError):
            size = 0
        records.append({
            "device": device, "path": "lemonade://" + name,
            "name": name, "kind": "lemonade/" + str(item.get("recipe") or "unknown")[:50],
            "size_bytes": size, "modified_ns": 0,
            "sha256": None,  # Lemonade publishes no file digest
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
