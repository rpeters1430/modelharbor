"""Read-only Ollama inventory collection and ModelHarbor upload."""
import argparse
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
        records.append({
            "device": device, "path": "ollama://" + name,
            "name": name, "kind": "ollama/" + str(details.get("family") or "unknown")[:50],
            "size_bytes": max(0, int(item.get("size") or 0)),
            "modified_ns": 0,
            # Manifest digest; the server compares it with the registry to spot updates.
            "sha256": digest if len(digest) == 64 else None,
        })
    return records

def main():
    # Preview only: modelharbor_agent.py uploads, under the device name in its config.
    parser = argparse.ArgumentParser(description="Preview the Ollama models the agent would find")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--dry-run", action="store_true", help="Accepted for older instructions; this script never uploads")
    args = parser.parse_args()
    with requests.Session() as session:
        models = collect(session, args.ollama_url, "preview:ollama")
    print(f"Found {len(models)} Ollama models")
    for model in models:
        print(model["name"], model["size_bytes"])

if __name__ == "__main__":
    main()
