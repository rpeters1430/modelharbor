# ModelHarbor

Self-hosted, mobile-friendly AI model inventory dashboard for **ComfyUI** and **Ollama**. Designed for a lightweight Docker deployment on NAS hardware, with a read-only Windows scanner.

## Features

- Responsive, dark-themed model inventory dashboard for mobile and desktop.
- Search, model type filters, device filters, and model detail views.
- SQLite-backed inventory API with token authentication.
- Windows ComfyUI scanner (read-only).
- Ollama model inventory scanner (if merged from its separate PR).
- Multi-platform Docker image published to GitHub Container Registry (GHCR).

> **Current scope:** Inventory dashboard with exact SHA-256 source identification (Civitai, Hugging Face) and update checks (Civitai, Ollama, Hugging Face). See [docs/SOURCES_AND_UPDATES.md](docs/SOURCES_AND_UPDATES.md). Approved updates can be downloaded next to the old file after a SHA-256 check.

## Docker Compose — UGREEN NAS / Dockhand

Use this Compose file in Dockhand or save it as `compose.yaml` in your ModelHarbor directory:

```yaml
services:
  modelharbor:
    image: ghcr.io/rpeters1430/modelharbor:latest
    container_name: modelharbor
    restart: unless-stopped
    ports:
      - "8096:8096"
    environment:
      MODELHARBOR_DB: /data/modelharbor.sqlite3
      MODELHARBOR_AGENT_TOKEN: ${MODELHARBOR_AGENT_TOKEN:?Set MODELHARBOR_AGENT_TOKEN in .env}
    volumes:
      - modelharbor_data:/data
    security_opt:
      - no-new-privileges:true

volumes:
  modelharbor_data:
```

**Important:** The Compose file reads `MODELHARBOR_AGENT_TOKEN` from a local `.env` file. In Dockhand, set this variable through the stack's environment configuration. Do not put your actual token in GitHub.

Create a strong random token (for example, with `openssl rand -hex 32`) and add it to `.env`:

```dotenv
MODELHARBOR_AGENT_TOKEN=replace-with-your-generated-secret
```

On the NAS over SSH:

```bash
mkdir -p /volume1/docker/modelharbor
cd /volume1/docker/modelharbor
# Save compose.yaml and .env here, then:
docker compose pull
docker compose up -d
```

Open **http://NAS-IP:8096/** on a trusted LAN and enter the configured token. The container exposes the dashboard and API on port **8096**.

The `modelharbor_data` named volume persists the SQLite database across container recreation and image updates. Back up this volume before major changes.

### Update from GHCR

After a new `main` build completes:

```bash
cd /volume1/docker/modelharbor
docker compose pull
docker compose up -d --remove-orphans
```

In **Dockhand**, use its pull/redeploy functionality on the ModelHarbor stack. If the package is private, authenticate to GHCR first. Public image pulls normally require no login.

**GHCR package visibility:** GitHub may create the package as private by default. If anonymous pulls fail, open the repository's package settings and make the container package public, or authenticate your NAS to GHCR.

### Security

- Restrict port 8096 to your trusted LAN or Tailscale. Do not expose the dashboard directly to the public internet.
- HTTP does not encrypt the API token. Use HTTPS through a trusted reverse proxy for remote access.
- The dashboard stores the API token in browser session storage.
- No installed models are changed by inventory scans.

## Windows inventory agents

Install Python 3.11+ and the agent dependencies:

```powershell
pip install -r agent/requirements.txt
python agent/scan.py --models-dir "C:\\path\\to\\ComfyUI\\models" --dry-run
```

For the Ollama scanner, see [Ollama setup](docs/OLLAMA.md) if available. See [Dashboard setup](docs/DASHBOARD.md) and [Phase 1 instructions](docs/PHASE1.md) for details.

## Container publishing

[Docker publish workflow](.github/workflows/docker-publish.yml) builds Linux AMD64 and ARM64 images. Pushes to `main` publish `latest` and a commit-SHA tag; version tags beginning with `v` publish matching version tags. Pull requests build but do not publish images.

## Roadmap

1. Read-only local inventory and mobile dashboard.
2. Ollama inventory and model metadata enrichment.
3. Hugging Face/Civitai discovery and source-aware version checks.
4. Verified, approval-based downloads and rollback.

Licensed as specified in the repository. No license is implied by this README.
