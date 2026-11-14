# ModelHarbor Linux agent

The same agent as on Windows ([Windows agent setup](WINDOWS_AGENT.md)). It runs as two systemd **user** units rather than scheduled tasks. Everything the Windows page says about status, **Scan now**, hash checks, downloads, deletes and conversions also applies here.

## Installation

1. Clone this repository on the Linux PC. You need Python 3.11+ with `venv`. On Debian or Ubuntu, that means `sudo apt install python3-venv`.
2. Create the config folder and copy the example config:
   ```bash
   mkdir -p ~/.config/ModelHarbor
   cp agent/config.example.json ~/.config/ModelHarbor/agent.json
   ```
   Edit these settings:
   - `server`: the ModelHarbor URL on the NAS.
   - `device`: a name for this PC on the dashboard.
   - `lemonade_enabled` and `lmstudio_enabled`: set each to `false` unless that server runs here.

   Leave `comfyui_models_dirs` empty to auto-detect it. The agent finds `~/ComfyUI/models` and any `~/<folder>/ComfyUI/models`, such as `~/AI/ComfyUI/models`. Otherwise list the full path of the `models` folder.
3. Put the NAS token, and optionally your provider keys, in `~/.config/ModelHarbor/agent.env`. Never put them in `agent.json`:
   ```bash
   MODELHARBOR_AGENT_TOKEN=the-same-secret-as-the-nas
   HF_TOKEN=hf_...            # optional: gated Hugging Face downloads
   CIVITAI_API_TOKEN=...      # optional: Civitai downloads that need a login
   ```
4. Install and start the units with `bash agent/install-service.sh`. The script does four things:
   - It creates `agent/.venv` and installs `requests` into it.
   - It makes `agent.env` readable only by you.
   - It installs `modelharbor-sync.timer`, which syncs at startup and then every 30 minutes.
   - It installs `modelharbor-worker.service`, which carries out dashboard requests within about 20 seconds.
5. Follow the logs with `journalctl --user -u modelharbor-worker -u modelharbor-sync -f`. To sync now, run `systemctl --user start modelharbor-sync`.

The units run while you are logged in. To keep them running after you log out, or start them before you log in, run `sudo loginctl enable-linger $USER`.

To test without installing:

```bash
python3 -m venv agent/.venv && agent/.venv/bin/pip install -r agent/requirements.txt
agent/.venv/bin/python agent/modelharbor_agent.py --once --dry-run
```

## Where downloads go

A download from **Browse** or **Inspect** goes into the folder the dashboard shows, under the first models root. For example, an SDXL LoRA goes to `models/loras/SDXL`, a checkpoint to `models/checkpoints/<family>`, and a text encoder to `models/text_encoders`. ComfyUI picks up the new file the next time you refresh its model lists, with no restart needed.

Linux folder names are case-sensitive. If a folder such as `loras/sdxl` already exists, the download goes there instead of creating a separate `loras/SDXL`. An update download is saved next to the file it replaces.

## Hardware and conversions

The agent reports the GPU with the most dedicated memory. For AMD and Intel cards it reads this from sysfs, and for NVIDIA cards from `nvidia-smi`. The dashboard uses it for fit ratings and the **Convert** tab. Integrated GPUs are ignored.

Conversions use the Python in ComfyUI's own `.venv` or `venv`, next to the models folder. They run under `nice` so ComfyUI stays responsive. Set `"convert_python": "/path/to/ComfyUI/.venv/bin/python"` in `agent.json` if the agent doesn't find it.
