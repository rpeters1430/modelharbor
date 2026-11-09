# ModelHarbor

Self-hosted, mobile-friendly AI model inventory and management dashboard for **ComfyUI**, **Ollama**, **Lemonade Server** and **LM Studio**. Designed for a lightweight Docker deployment on NAS hardware, with a read-only Windows scanner.

## Features

- Responsive, dark-themed model inventory dashboard for mobile and desktop.
- Search, model type filters, device filters, and model detail views.
- SQLite-backed inventory API with token authentication.
- Windows ComfyUI scanner (read-only).
- Convert tab: shrink diffusion models and text encoders that don't fit the GPU to ComfyUI's `int8_convrot` format on the PC, and recognise your own conversions. See [docs/WINDOWS_AGENT.md](docs/WINDOWS_AGENT.md).
- Ollama, Lemonade and LM Studio inventory from their local APIs, with one-click import of Hugging Face GGUF files. Each is shown only while the PC's agent can reach it. LM Studio also supports queued load/unload actions; see [LM Studio setup](docs/LMSTUDIO.md).
- Multi-platform Docker image published to GitHub Container Registry (GHCR).

> **Current scope:** Exact SHA-256 source identification applies to files with accessible local hashes. Runtime-managed Ollama, Lemonade and LM Studio models use their respective APIs; LM Studio downloads are provider-managed and are not SHA-256 verified by ModelHarbor. See [docs/SOURCES_AND_UPDATES.md](docs/SOURCES_AND_UPDATES.md).

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
python agent/scan.py --models-dir "C:\\path\\to\\ComfyUI\\models"
```

That only previews what is found. `agent/modelharbor_agent.py` does the uploading; see [Windows agent setup](docs/WINDOWS_AGENT.md). For LLM servers, see [Lemonade setup](docs/LEMONADE.md), [Ollama setup](docs/OLLAMA.md) and [LM Studio setup](docs/LMSTUDIO.md). See [Dashboard setup](docs/DASHBOARD.md) and [Phase 1 instructions](docs/PHASE1.md) for details.

## Container publishing

[Docker publish workflow](.github/workflows/docker-publish.yml) builds Linux AMD64 and ARM64 images. Pushes to `main` publish `latest` and a commit-SHA tag; version tags beginning with `v` publish matching version tags. Pull requests build but do not publish images.

## Roadmap

- **ModelHarbor 2.0 foundation:** LM Studio inventory, Hugging Face GGUF download, and explicit load/unload controls. See [LM Studio setup](docs/LMSTUDIO.md).
- **Model sets:** save named collections and see missing or unknown model requirements per device/runtime.
- **Fit and storage insight:** add explainable context-aware LLM fit estimates and fleet-wide duplicate/storage reporting.
- **Job recovery:** improve reconciliation and safe controls for long-running operations.

Licensed as specified in the repository. No license is implied by this README.
