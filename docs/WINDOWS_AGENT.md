# ModelHarbor Windows sync agent

The agent reads file metadata from configured ComfyUI model directories and calls Ollama's read-only `/api/tags` endpoint. It uploads inventory to the NAS and sends a heartbeat. It never deletes, renames, or downloads model files.

## Installation

1. Download/clone this repository on your Windows PC and install Python 3.11+.
2. In PowerShell, run `python -m pip install -r agent/requirements.txt`.
3. Copy `agent/config.example.json` to `$env:APPDATA\ModelHarbor\agent.json`. Create the folder first.
4. Set `server` to your actual NAS dashboard URL and configure `comfyui_models_dirs` to the directory/directories containing your `checkpoints`, `loras`, etc. Empty list tries common paths but **ComfyUI Desktop paths vary**.
5. Set the same API token as the NAS, stored in your Windows **user** environment (not in the JSON config):
   `[Environment]::SetEnvironmentVariable("MODELHARBOR_AGENT_TOKEN", "YOUR_LONG_SECRET", "User")`.
6. Open a **new PowerShell session** and test: `python agent/modelharbor_agent.py --once --dry-run`, then `python agent/modelharbor_agent.py --once`.
7. To schedule at logon and periodically, run `powershell -ExecutionPolicy Bypass -File agent/install-task.ps1` from the repository root. Check Task Scheduler history and run the task manually once.

The Windows scheduled task uses your user account. It does not require administrator privileges. When the PC is off, heartbeats stop and the dashboard will eventually show it as offline.

## Status and "Scan now"

The dashboard's **Devices** tab shows each PC's live state: scanning folders, hashing new or changed files (file N of M, GB done, current file, with a progress bar), uploading, up to date (with when), or the last error. It also shows whether the background task (`ModelHarbor Hash Worker`) is running.

**Scan now** asks that background task to start a sync. It checks for requests about every 20 seconds, so the scan starts shortly after. If the task isn't running, the request waits until it is. A scheduled sync and a requested one never run at the same time: whichever starts second is skipped.

## Converting models that do not fit the GPU

The dashboard's **Convert** tab lists 16- and 32-bit diffusion models and text encoders that are bigger than the PC's VRAM (less 2 GB for ComfyUI's working memory). **Convert** asks the background task to write a copy in ComfyUI's `int8_convrot` format next to the original, named `<name>_int8_convrot.safetensors` (a `bf16`/`fp16`/`fp32` in the name is replaced). It is about half the size and loads in ComfyUI like any model.

- The conversion runs `agent/convert_int8.py` with **ComfyUI's own Python**, which already has torch, safetensors and ComfyUI's quantizer (`comfy_kitchen`). The agent finds ComfyUI Desktop's and the portable build's Python on its own; otherwise set `"convert_python": "C:\path\to\ComfyUI\.venv\Scripts\python.exe"` in `agent.json`. `"converts_enabled": false` turns conversions off.
- Only linear weights inside the transformer blocks are quantized; embeddings, heads and norms keep their precision, as in the official `int8_convrot` releases. Every layer is checked after quantizing, and the model is left alone if any layer loses more than 5% precision.
- The original is re-hashed first and never changed. The copy inherits the original's Civitai or Hugging Face link, so its family, trigger words and update alerts carry over. When you are happy with it, **Delete original** frees the space.
- It runs at low priority on the CPU, so ComfyUI stays usable; a 12B model takes a few minutes and needs free RAM of about the model's size. The background task does not pick up other jobs (downloads, deletes) until it finishes.

Files you converted yourself (named `…_int8_convrot`, `…_fp8`, etc.) are not on any site byte for byte, so they are not matched automatically. Open one in the Library: ModelHarbor searches Civitai for the model it was made from and offers **Looks like your conversion of this file**, which links it in one tap (shown as linked by hand, not as a verified match).

## Notes
- The first sync after updating hashes every model file once (see `docs/SOURCES_AND_UPDATES.md`); later syncs only hash new or changed files.

- Use the real port from your Dockhand stack (for example, 8099 if you mapped 8099:8096).
- The server must be reachable from the Windows PC.
- A model's size is counted per inventory record, so shared files may be counted more than once.
- The agent skips missing ComfyUI directories rather than deleting their previously uploaded inventory.
- A reachable but empty Ollama inventory will replace previous Ollama entries with an empty list.
- Restrict HTTP access to trusted LAN; for remote access use HTTPS/Tailscale.
