# Ollama inventory — Phase 2

The Windows agent can query the Ollama `/api/tags` endpoint and upload model inventory to ModelHarbor. No model pull, delete, or update operations are performed.

## Dry run (Windows PowerShell)

```powershell
python agent/ollama_scan.py --ollama-url http://127.0.0.1:11434 --dry-run
```

## Upload to NAS

Set the same `MODELHARBOR_AGENT_TOKEN` used by the NAS container, then:

```powershell
python agent/ollama_scan.py --ollama-url http://127.0.0.1:11434 --server http://192.168.8.137:8096
```

The Ollama inventory uses a distinct device suffix (`:ollama`) so it cannot overwrite the ComfyUI scan for the same machine.

## Current limitations

This first integration captures name, family, and size. Digest and quantization metadata are not yet persisted in the backend schema; a follow-up schema migration will add those fields before implementing version comparisons. Ollama is queried locally by default; avoid exposing its unauthenticated API to untrusted networks.
