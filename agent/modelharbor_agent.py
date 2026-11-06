"""Windows-friendly read-only background inventory sync. Standard library + requests."""
import argparse
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
import requests
from scan import scan
from ollama_scan import collect
import lemonade_scan
from hash_worker import process_one
from hash_cache import fill_hashes
from hardware import detect as detect_hardware
import download_worker
import convert_worker
from status import NullReporter, Reporter, acquire_lock, release_lock

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

def sync(cfg, token, dry_run=False):
    """One inventory sync with dashboard progress; skipped if another sync holds the lock."""
    device = cfg.get("device") or socket.gethostname()
    lock = DEFAULT_CONFIG.with_name("sync.lock")
    if not dry_run and not acquire_lock(lock):
        LOG.info("Another sync is already running; skipping")
        return
    with requests.Session() as session:
        reporter = NullReporter() if dry_run else Reporter(session, cfg["server"], token, device)
        try:
            run_once(cfg, token, dry_run, reporter)
        except Exception as exc:
            reporter.update("error", message=str(exc)[:300])
            raise
        finally:
            if not dry_run:
                release_lock(lock)

def run_once(cfg, token, dry_run=False, reporter=None):
    reporter = reporter or NullReporter()
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
            reporter.update("scanning", current=str(root), done=len(comfy))
            comfy.extend(scan(Path(root), device + ":comfyui"))
        if cfg.get("hash_models", True) and not dry_run:
            def hashing(done, total, done_bytes, total_bytes, path):
                reporter.update("hashing", done=done, total=total, done_bytes=done_bytes,
                                total_bytes=total_bytes, current=path)
            fill_hashes(comfy, cfg.get("hash_cache") or DEFAULT_CONFIG.with_name("hashes.json"), progress=hashing)
        ollama = None
        if cfg.get("ollama_enabled", True):
            try:
                ollama = collect(session, cfg.get("ollama_url", "http://127.0.0.1:11434"), device + ":ollama")
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("Ollama unavailable; preserving previous inventory: %s", exc)
        lemonade = lemonade_info = None
        if cfg.get("lemonade_enabled", True):
            lemonade_url = cfg.get("lemonade_url", lemonade_scan.DEFAULT_URL)
            try:
                lemonade = lemonade_scan.collect(session, lemonade_url, device + ":lemonade")
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("Lemonade unavailable; preserving previous inventory: %s", exc)
            if lemonade is not None:
                try:
                    lemonade_info = lemonade_scan.system_info(session, lemonade_url)
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    LOG.warning("Lemonade system info unavailable: %s", exc)
        LOG.info("Found %d ComfyUI models, %s Ollama models and %s Lemonade models", len(comfy),
                 len(ollama) if ollama is not None else "unavailable", len(lemonade) if lemonade is not None else "unavailable")
        llms = (ollama or []) + (lemonade or [])
        if dry_run:
            for m in (comfy + llms)[:25]:
                print(m["kind"], m["name"])
            return
        reporter.update("uploading", total=len(comfy) + len(llms))
        if roots and any(Path(root).is_dir() for root in roots):
            post_inventory(session, server, token, device + ":comfyui", comfy)
        elif roots:
            LOG.warning("No configured ComfyUI roots accessible; preserving prior inventory")
        else:
            LOG.warning("No ComfyUI roots discovered; set comfyui_models_dirs in config")
        # A runtime switched off in config is gone from this PC: clear its old inventory.
        for runtime, found in (("ollama", ollama), ("lemonade", lemonade)):
            if found is not None or not cfg.get(runtime + "_enabled", True):
                post_inventory(session, server, token, device + ":" + runtime, found or [])
        # Heartbeat is independent of inventory; never modifies model files.
        response = session.post(server.rstrip("/") + "/api/agents/heartbeat",
            headers={"X-ModelHarbor-Token": token},
            json={"device": device, "comfyui_count": len(comfy),
                  "ollama_count": len(ollama) if ollama is not None else None,
                  "lemonade_count": len(lemonade) if lemonade is not None else None,
                  "lemonade": lemonade_info,
                  "hardware": detect_hardware()}, timeout=20)
        response.raise_for_status()
        reporter.update("idle", done=len(comfy), total=len(comfy), message=f"Synced {len(comfy)} ComfyUI files"
                        + "".join(f", {len(found)} {label} models" for label, found in
                                  (("Ollama", ollama), ("Lemonade", lemonade)) if found is not None))


def _lane(name, interval, steps, stop):
    """Poll one kind of dashboard job until stopped, on its own thread and requests session.

    Each step returns True when it handled a job; the lane then polls again at once to drain its queue."""
    with requests.Session() as session:
        while not stop.is_set():
            busy = False
            for label, step in steps:
                try:
                    busy = bool(step(session)) or busy
                except Exception as exc:  # noqa: BLE001 - keep the lane alive; retried next poll
                    LOG.warning("%s poll failed; retrying: %s", label, exc)
            if not busy:
                stop.wait(interval)


def worker_lanes(cfg, token):
    """{lane name: [(label, step)]}. Long jobs get their own lane, so a multi-hour download or conversion
    never holds up a hash check, a delete or a scan requested from the dashboard."""
    device = (cfg.get("device") or socket.gethostname()) + ":comfyui"
    roots = cfg.get("comfyui_models_dirs", []) or discover_roots()
    server, agent = cfg["server"], device.rsplit(":", 1)[0]

    def scan_request(session):
        res = session.get(server.rstrip("/") + "/api/agents/" + requests.utils.quote(agent, safe="") + "/scan-request",
                          headers={"X-ModelHarbor-Token": token}, timeout=20)
        if res.ok and res.json().get("scan"):
            LOG.info("Scan requested from the dashboard")
            sync(cfg, token)
        return False  # not a queue to drain

    control = [("Scan request", scan_request)]
    if cfg.get("deletes_enabled", True):
        control.append(("Delete", lambda s: download_worker.process_delete(s, server, token, agent, roots)))
    if cfg.get("downloads_enabled", True):
        control.append(("Cleanup", lambda s: download_worker.process_cleanup(s, server, token, agent, roots)))
    lanes = {"control": control,
             "hashes": [("Hash job", lambda s: process_one(s, server, token, device, roots))]}
    if cfg.get("downloads_enabled", True):
        lanes["downloads"] = [("Download", lambda s: download_worker.process_one(
            s, server, token, agent, roots, cfg.get("ollama_url", "http://127.0.0.1:11434"),
            cfg.get("lemonade_url", lemonade_scan.DEFAULT_URL), cfg.get("lemonade_load_after_pull", True)))]
    if cfg.get("converts_enabled", True):
        lanes["converts"] = [("Conversion", lambda s: convert_worker.process_convert(s, server, token, agent, roots, cfg))]
    return lanes


def run_hash_worker(cfg, token, stop=None):
    """Continuous worker for dashboard requests: scans, hash checks, deletes, downloads and conversions,
    each kind on its own thread."""
    if not cfg.get("hash_verification_enabled", True):
        LOG.info("Hash verification worker disabled in config")
        return
    interval = max(10, int(cfg.get("hash_poll_seconds", 20)))
    stop = stop or threading.Event()
    threads = [threading.Thread(target=_lane, args=(name, interval, steps, stop), name="lane-" + name, daemon=True)
               for name, steps in worker_lanes(cfg, token).items()]
    for thread in threads:
        thread.start()
    try:
        while any(t.is_alive() for t in threads):
            for thread in threads:
                thread.join(1)
    except KeyboardInterrupt:
        stop.set()

def main():
    p = argparse.ArgumentParser(description="ModelHarbor background inventory sync")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--once", action="store_true")
    p.add_argument("--hash-worker", action="store_true", help="Poll for approved SHA-256 verification jobs; runs continuously")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    token = os.environ.get("MODELHARBOR_AGENT_TOKEN", "")
    if not args.dry_run and not token:
        p.error("MODELHARBOR_AGENT_TOKEN must be set in the Windows environment")
    if args.hash_worker:
        if args.dry_run or args.once:
            p.error("--hash-worker runs continuously and cannot use --once or --dry-run")
        run_hash_worker(cfg,token)
        return
    interval = max(300, int(cfg.get("sync_interval_seconds", 1800)))
    while True:
        try:
            sync(cfg, token, args.dry_run)
        except (requests.RequestException, OSError, ValueError) as exc:
            LOG.error("Sync failed (will retry): %s", exc)
        if args.once or args.dry_run:
            break
        time.sleep(interval)

if __name__ == "__main__":
    main()
