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

## Import a Hugging Face GGUF into Ollama

In Browse, paste a Hugging Face repository or file link. Choose Ollama (import GGUF) as the destination, then select a supported single-file .gguf model. ModelHarbor asks the Windows agent to call the PC's local Ollama /api/pull; Ollama downloads and verifies the model itself. The Browse row shows the pull status and recognizes the model after the Ollama inventory sync.

The import uses the exact GGUF filename as the Hugging Face tag (for example, hf.co/owner/repo:model-Q4_K_M.gguf), so files with different names remain selectable even when they share a quantization. If a repository contains duplicate filenames in different folders, the import is rejected because Ollama identifies the file by its filename tag.

Only single-file GGUF model files are offered to Ollama. Non-GGUF files, mmproj projectors, and split GGUF shards must use a different import workflow. Public repositories work directly. For private Hugging Face repositories, follow Ollama's Hugging Face instructions (https://huggingface.co/docs/hub/en/ollama) to configure repository access for Ollama; setting a token only in ModelHarbor does not authenticate Ollama's own pull.

Ollama is queried locally by default; avoid exposing its unauthenticated API to untrusted networks.
