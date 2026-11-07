# ModelHarbor 2.0 — LM Studio Connector Implementation Plan

> **For agentic workers:** This is the first independently shippable milestone in [the ModelHarbor 2.0 product design](../specs/modelharbor-2.0.md). Execute task-by-task on the feature branch; do not merge directly to `main`.

**Goal:** Add LM Studio inventory and user-requested model download/load/unload from the existing ModelHarbor dashboard.

**Architecture:** Keep the NAS as the inventory and job coordinator. The Windows agent connects to an explicitly configured LM Studio REST endpoint on the runtime host, normalizes its model inventory, and performs provider-specific operations locally with an environment-stored API key. Use LM Studio's native `/api/v1` management endpoints and keep all other runtime inventories isolated.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite, static HTML/CSS/JavaScript, existing Windows agent and Docker deployment.

**Spec:** `docs/superpowers/specs/modelharbor-2.0.md`

## Global Constraints

- LM Studio configuration is opt-in and the endpoint URL is supplied explicitly by the user.
- Store `LMSTUDIO_API_KEY` in the Windows user environment; never place it in `agent.json`, the NAS database, or logs.
- Use `/api/v1/models`, `/api/v1/models/download`, `/api/v1/models/download/status`, `/api/v1/models/load`, and `/api/v1/models/unload`; do not route inference through ModelHarbor.
- Use a separate inventory identity ending in `:lmstudio`; never overwrite ComfyUI, Ollama, or Lemonade inventory.
- Do not automatically remove, overwrite, move, or load a model during inventory or Browse.
- Preserve existing LM Studio model metadata and explicitly report unknown values rather than inventing them.
- Do not add new runtime dependencies.

## Review Focus

- **LM Studio offline or API server disabled:** keep the last inventory and report the connection failure without clearing models.
- **API key enabled or rejected:** return a readable authentication error without exposing the key.
- **Multiple loaded instances for one model:** preserve instance IDs so an unload request targets the instance the user selected.
- **Download API returns already-downloaded, failed, or no job ID:** reconcile each result without polling a missing job or starting a duplicate pull.
- **Unsupported model format or endpoint version:** disable the action with an explanation while leaving inventory visible.

---

### Task 1: Add read-only LM Studio inventory collection

**Files:**
- Create: `agent/lmstudio_scan.py`
- Modify: `agent/config.example.json`

**Interfaces:**
- Produces `collect(session, base_url, device) -> list[dict]`.
- Produces `auth_headers() -> dict`, reading `LMSTUDIO_API_KEY` from the process environment.
- Each record uses a unique path such as `lmstudio://{model_key}`, `kind=lmstudio/{type}`, reported `size_bytes`, `checkpoint=model_key`, and supported metadata including architecture, quantization, context limit, format, and loaded instances.
- Config adds `lmstudio_enabled` (default `false`) and `lmstudio_url` (default `http://127.0.0.1:1234`).

- [x] Normalize `GET /api/v1/models` responses for LLMs, embedding models, and decision models.
- [x] Keep loaded instance identifiers and configured context length in a JSON-safe inventory field.
- [x] Reject malformed model records without aborting the entire scan.
- [x] Use bounded HTTP timeouts and produce a readable 401/403 message that names the environment variable, never its value.

**Completion check:** A dry-run scan produces stable `:lmstudio` records from representative documented API responses and leaves the local model store untouched.

### Task 2: Integrate LM Studio into agent sync and device status

**Files:**
- Modify: `agent/modelharbor_agent.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/static/app.js`
- Modify: `backend/app/static/index.html`
- Modify: `backend/app/static/styles.css`

**Interfaces:**
- The agent posts inventory under `{device}:lmstudio` and includes `lmstudio_count` in heartbeat.
- The heartbeat schema stores LM Studio endpoint host and connection status separately from Lemonade hardware data.
- The Devices view displays the LM Studio runtime and its latest count/status.

- [x] Add optional LM Studio collection without changing current ComfyUI/Ollama/Lemonade failure behavior.
- [x] Preserve existing LM Studio inventory if the endpoint is temporarily unavailable.
- [x] When the user disables LM Studio, clear only the `:lmstudio` inventory for that agent.
- [x] Show LM Studio as its own source in the Devices view and report its last contact.

**Completion check:** Enabling/disabling or losing the LM Studio endpoint cannot change other runtime records or their device status.

### Task 3: Add agent-side LM Studio download and load/unload operations

**Files:**
- Modify: `agent/download_worker.py`
- Modify: `agent/modelharbor_agent.py`
- Modify: `agent/config.example.json`

**Interfaces:**
- Provider download worker uses LM Studio's documented REST API and keeps the current job progress/result contract.
- A download is addressed by a Hugging Face repository URL plus a selected quantization; it is not treated as a direct file download with a verified local SHA-256.
- Load accepts the LM Studio model key and an optional context length within the server-reported maximum.
- Unload accepts the selected loaded instance ID.

- [x] Implement download job creation, status reconciliation, and progress reporting for documented LM Studio status values.
- [x] Handle `already_downloaded` as a successful no-op and avoid duplicate jobs when a job is still active.
- [x] Implement explicit load and unload requests; do not load as a download side effect unless the user selected “download and load.”
- [x] Map provider errors to bounded user-visible messages.
- [x] Keep authorization headers scoped to the configured endpoint and omit them from logs/results.

**Completion check:** Each LM Studio action maps to one user request and one runtime-confirmed result; failures do not delete the downloaded model.

### Task 4: Expose LM Studio actions through the API and dashboard

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/downloads.py`
- Modify: `backend/app/static/index.html`
- Modify: `backend/app/static/app.js`
- Modify: `backend/app/static/styles.css`

**Interfaces:**
- Browse adds an LM Studio destination for supported GGUF model listings.
- Download jobs carry the source repository and quantization needed by the agent without storing the LM Studio API key.
- Model details show loaded/unloaded status and offer explicit Load/Unload actions.
- Load configuration exposes a context length with a default taken from current runtime configuration when available.

- [x] Add LM Studio to Browse only for supported single-repository GGUF downloads and show the chosen quantization.
- [x] Display a review step with target device, model, quantization, and estimated bytes before queueing.
- [x] Add load/unload controls for models with a valid key and instance state.
- [x] Refresh inventory/status after completed operations.
- [x] Display “not available” for unsupported operations instead of silently falling back to another provider.

**Completion check:** Users can tell which PC/runtime will receive an action and can distinguish queued, running, completed, and failed operations.

### Task 5: Document setup and correct release overview

**Files:**
- Create: `docs/LMSTUDIO.md`
- Modify: `README.md`

- [x] Document opt-in configuration, supported LM Studio API version, server startup, remote endpoint use, API-key environment setup, and troubleshooting.
- [x] Document that the native API download is managed by LM Studio and is not the same as ModelHarbor's SHA-verified direct file download.
- [x] Update README features and roadmap to reflect current ComfyUI, Ollama, Lemonade, source browsing, verified downloads, conversions, and this connector.
- [x] Keep the LAN/Tailscale security guidance consistent with the API-key recommendation for remotely accessible LM Studio.

**Completion check:** A user following the README and LM Studio guide can configure the connector without guessing a URL or exposing an API key in a config file.

---

## Follow-on plans

These approved 2.0 areas remain in the product design and should be planned as separate, independently reviewable work after the connector milestone:

1. Saved model sets and per-device/runtime readiness.
2. Context-aware LLM fit estimates and fleet duplicate/storage reporting.
3. Recovery and safe controls for existing long-running jobs.

## Plan handoff

This plan chooses the native, single-implementer workflow because the LM Studio connector shares agent, API, job, and dashboard interfaces. Product behavior remains bounded by the approved design. The implementation does not include a test authoring or test execution step.
