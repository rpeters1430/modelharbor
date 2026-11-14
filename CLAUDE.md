# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ModelHarbor is a self-hosted AI model inventory dashboard. It has two halves that talk over HTTP:

- **`backend/`**: a FastAPI + SQLite server that runs in Docker on a NAS (port 8096). It serves the API and a vanilla-JS PWA dashboard (`backend/app/static/`, no build step).
- **`agent/`**: Python scripts that run on the user's PC (Windows or Linux). They scan ComfyUI model folders and the local LLM servers (Ollama, Lemonade, LM Studio), upload the inventory, and carry out jobs the dashboard queues.

User-facing behaviour is documented in `README.md` and `docs/*.md`. Keep those docs in step when behaviour changes. `docs/superpowers/` holds design specs and plans.

## Commands

Run all commands from the repository root. The tests import `backend.app.*` as a package and `agent.*` from the root.

```bash
pip install -r backend/requirements.txt -r agent/requirements.txt pytest httpx   # same as CI
python -m pytest -q                                   # full suite (this is all CI runs; there is no linter)
python -m pytest tests/test_downloads.py -q           # one file
python -m pytest tests/test_api.py::test_health -q    # one test

# Run the server locally (the DB defaults to /data/..., so override it)
MODELHARBOR_DB=./dev.sqlite3 MODELHARBOR_AGENT_TOKEN=dev MODELHARBOR_SOURCE_CHECKS=0 \
  uvicorn backend.app.main:app --port 8096

# Agent (config: %APPDATA%\ModelHarbor\agent.json on Windows, ~/.config/ModelHarbor/agent.json on Linux; see agent/config.example.json)
python agent/modelharbor_agent.py --once --dry-run
python agent/scan.py --models-dir <ComfyUI models dir>   # preview only, never uploads
```

CI (`.github/workflows/ci.yml`) uses Python 3.15. The Docker image (`backend/Dockerfile`) copies only `backend/requirements.txt` and `backend/app`. Pushes to `main` publish the multi-arch image to GHCR (`docker-publish.yml`).

## Environment variables (backend)

- `MODELHARBOR_AGENT_TOKEN` is the shared secret. Every `/api/*` route except `/api/health` checks the `X-ModelHarbor-Token` header with `authorize()` in `main.py`.
- `MODELHARBOR_DB` is the SQLite path. Tests monkeypatch `main.DB` to a `tmp_path` file.
- `MODELHARBOR_SOURCE_CHECKS=0` turns off the background thread that checks for upstream updates (`sources.start_worker`, started in the FastAPI lifespan). Tests use `TestClient(app)` without a context manager, so the lifespan and the worker never run in tests.

## Architecture

**The NAS decides, the PC acts.** The backend never touches model files. Any action that changes files (hash verification, downloads, deleting an old version after an update, deleting a model, int8 conversion, LM Studio load/unload) works like this:
1. The dashboard POSTs a request, and the backend writes a job row in `queued` state.
2. The agent polls a `.../next?agent=<device>` endpoint to claim the job, does the work locally, then POSTs progress and a result.
3. The job states are `queued → running → done | failed`. Jobs that stay active too long are treated as stale (for example, `STALE_SECONDS` in `downloads.py`).

Each job type has its own backend module that owns its tables (`downloads.py`, `deletes.py`, `converts.py`, `hash_matches.py`), plus a matching agent worker (`download_worker.py`, `hash_worker.py`, `convert_worker.py`). `main.py` holds the Pydantic request models and thin route handlers that call into those modules.

**Schema migrations are additive and inline.** There are no migration files. Each module's `ensure_tables(db)` runs `CREATE TABLE IF NOT EXISTS`, then adds missing columns with `ALTER TABLE ... ADD COLUMN` after checking `PRAGMA table_info`. `main._migrate` does the same for the core `models`, `agent_heartbeats` and `agent_status` tables. A new column must be added the same way, so that existing NAS databases still upgrade (see `test_inventory_upgrades_old_database`).

**Device naming.** Inventory rows are keyed by `(device, path)`. A device is `<agent>:<runtime>`, such as `pc:comfyui`. `intelligence.physical_device` maps a device back to its agent. `GET /api/models` hides runtime models when the agent's heartbeat says it can't reach that LLM server (`_unreachable_runtimes`).

**Model enrichment.** `intelligence.enrich` infers family and type. `sources.annotate` attaches the upstream source (Civitai or Hugging Face, matched by SHA-256 or by a link the user chose) and any update available. `fit.py` estimates whether a model fits the GPU. `providers.py`, `browse.py`, `hf_repository.py` and `hf_inspect.py` call outside catalogues (HF, ModelScope, Civitai) for discovery and the Browse tab.

**Agent worker lanes.** `modelharbor_agent.py` either syncs once (`--once`) or loops. The background task (`--hash-worker`) runs `worker_lanes()`, which starts one thread per lane: `scan`, `control` (deletes and cleanups), `hashes`, `downloads` and `converts`. A long download or conversion therefore never blocks a scan or a delete. A lane drains its queue, then waits for the poll interval. `hash_cache.py` caches file hashes so a rescan only rehashes changed files. `convert_int8.py` runs under **ComfyUI's own Python**, not the agent's.

**Platforms.** The agent supports both Windows and Linux. The platform-specific parts are `config_dir()`/`discover_roots()` in `modelharbor_agent.py`, `hardware.py` (the Windows registry, or Linux sysfs and `nvidia-smi`), `convert_worker.find_python`, and the service installers: `install-task.ps1` uses Task Scheduler, and `install-service.sh` installs systemd user units with the token in `~/.config/ModelHarbor/agent.env`. Code that compares file names must allow for Linux paths being case-sensitive (see `download_worker.existing_folder`). User-facing text says "the PC's agent", not "Windows".

**Safety rules the code relies on**: the agent never overwrites a file. A download is written next to the old file and checked against the SHA-256 the NAS expects. An old file is deleted only after the user asks, and only once the new file is verified. Download file names must pass `downloads.SAFE_NAME`.

**Frontend.** `static/app.js` is a single IIFE with no framework. The token is kept in `localStorage` or `sessionStorage`, and every storage access is guarded. `sw.js` deliberately caches nothing, and the middleware sends `Cache-Control: no-cache` on dashboard files, so a deploy shows up straight away.

## Test conventions

Tests that cover agent scripts which aren't imported as a package add `agent/` to `sys.path` and then import them directly (`import modelharbor_agent`, `from download_worker import ...`). Outbound HTTP is stubbed by passing fake `get` or `session` callables, or with `unittest.mock.patch`. Tests never hit the network.
