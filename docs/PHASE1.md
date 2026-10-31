# Phase 1 — read-only inventory

## NAS
Copy `.env.example` to `.env`, set a random secret, then run `docker compose up -d --build`.
The API listens on port 8096. Restrict access to trusted LAN/Tailscale clients.
Health: `http://NAS-IP:8096/api/health`.

## Windows
Install Python 3.11+ and `pip install -r agent/requirements.txt`.
Run a dry scan first:

```powershell
python agent/scan.py --models-dir "C:\\path\\to\\ComfyUI\\models" --dry-run
```

Set `MODELHARBOR_AGENT_TOKEN` in the Windows environment, then omit `--dry-run` and pass `--server http://NAS-IP:8096`.
The scanner reads files' metadata only; it does not hash or modify large model files.

## Known limitations
This initial implementation scans one ComfyUI models root, does not follow symlinks, and does not yet integrate Ollama, Hugging Face, Civitai, updates, downloads, or a web UI. API token protects inventory endpoints but transport is HTTP; use a trusted LAN or TLS reverse proxy. Model inventory paths are sensitive metadata.
