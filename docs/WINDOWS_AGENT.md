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

## Notes
- The first sync after updating hashes every model file once (see `docs/SOURCES_AND_UPDATES.md`); later syncs only hash new or changed files.

- Use the real port from your Dockhand stack (for example, 8099 if you mapped 8099:8096).
- The server must be reachable from the Windows PC.
- A model's size is counted per inventory record, so shared files may be counted more than once.
- The agent skips missing ComfyUI directories rather than deleting their previously uploaded inventory.
- A reachable but empty Ollama inventory will replace previous Ollama entries with an empty list.
- Restrict HTTP access to trusted LAN; for remote access use HTTPS/Tailscale.
