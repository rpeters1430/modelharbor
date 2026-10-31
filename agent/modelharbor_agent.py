"""Windows-friendly read-only background inventory sync. Standard library + requests."""
import argparse
import json
import logging
import os
import socket
import time
from pathlib import Path
import requests
from scan import scan
from ollama_scan import collect

LOG = logging.getLogger("modelharbor")
DEFAULT_CONFIG = Path(os.environ.get("APPDATA", str(Path.home()))) / "ModelHarbor" / "agent.json"

def discover_roots():
    """Find common model roots; custom installs should be set in config."""
    roots = []
    home = Path.home()
    for root in (home / "ComfyUI" / "models",
                 home / "Documents" / "ComfyUI" / "models",
                 home / "AppData" / "Roaming" / "ComfyUI" / "models"):
        if root.is_dir():
            roots.append(str(root))
    return roots

def load_config(path):
    if not path.exists():
        raise SystemExit(f"Missing config: {path}. Copy agent/config.example.json and edit paths.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg.get("server", "").startswith(("http://", "https://")):
        raise SystemExit("Config server must be an http(s) URL")
    return cfg

def post_inventory(session, server, token, device, models):
    response = session.post(server.rstrip("/") + "/api/inventory",
        headers={"X-ModelHarbor-Token": token},
        json={"device": device, "models": models}, timeout=120)
    response.raise_for_status()

def run_once(cfg, token, dry_run=False):
    device = cfg.get("device") or socket.gethostname()
    server = cfg["server"]
    with requests.Session() as session:
        roots = cfg.get("comfyui_models_dirs", [])
        # Empty list means auto-detect; explicit paths override detection.
        if not roots:
            roots = discover_roots()
        comfy = []
        for root in roots:
            if not Path(root).is_dir():
                LOG.warning("ComfyUI models directory missing: %s", root)
                continue
            comfy.extend(scan(Path(root), device + ":comfyui"))
        ollama = None
        if cfg.get("ollama_enabled", True):
            try:
                ollama = collect(session, cfg.get("ollama_url", "http://127.0.0.1:11434"), device + ":ollama")
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("Ollama unavailable; preserving previous inventory: %s", exc)
        LOG.info("Found %d ComfyUI models and %s Ollama models", len(comfy), len(ollama) if ollama is not None else "unavailable")
        if dry_run:
            for m in (comfy + (ollama or []))[:25]:
                print(m["kind"], m["name"])
            return
        if roots and any(Path(root).is_dir() for root in roots):
            post_inventory(session, server, token, device + ":comfyui", comfy)
        elif roots:
            LOG.warning("No configured ComfyUI roots accessible; preserving prior inventory")
        else:
            LOG.warning("No ComfyUI roots discovered; set comfyui_models_dirs in config")
        if ollama is not None:
            post_inventory(session, server, token, device + ":ollama", ollama)
        # Heartbeat is independent of inventory; never modifies model files.
        response = session.post(server.rstrip("/") + "/api/agents/heartbeat",
            headers={"X-ModelHarbor-Token": token},
            json={"device": device, "comfyui_count": len(comfy),
                  "ollama_count": len(ollama) if ollama is not None else None}, timeout=20)
        response.raise_for_status()

def main():
    p = argparse.ArgumentParser(description="ModelHarbor background inventory sync")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    token = os.environ.get("MODELHARBOR_AGENT_TOKEN", "")
    if not args.dry_run and not token:
        p.error("MODELHARBOR_AGENT_TOKEN must be set in the Windows environment")
    interval = max(300, int(cfg.get("sync_interval_seconds", 1800)))
    while True:
        try:
            run_once(cfg, token, args.dry_run)
        except (requests.RequestException, OSError, ValueError) as exc:
            LOG.error("Sync failed (will retry): %s", exc)
        if args.once or args.dry_run:
            break
        time.sleep(interval)

if __name__ == "__main__":
    main()
