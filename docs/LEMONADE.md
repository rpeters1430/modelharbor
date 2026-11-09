# Lemonade Server

The Windows agent can list the models in [Lemonade Server](https://lemonade-server.ai) and, when you ask from Browse, have Lemonade download a Hugging Face GGUF and load it.

## Configure the agent

In `%APPDATA%\ModelHarbor\agent.json`:

```json
"lemonade_enabled": true,
"lemonade_url": "http://127.0.0.1:13305",
"lemonade_load_after_pull": true
```

If Lemonade runs on another machine, point `lemonade_url` at it (for example `http://192.168.8.143:13305`). Lemonade 9 and later listen on port 13305. Older releases used 8000, so if yours does, set `lemonade_url` to `http://127.0.0.1:8000`. Set `lemonade_load_after_pull` to `false` if you only want the download.

If you've removed Ollama, set `"ollama_enabled": false`. On the next sync the old Ollama models are cleared from the dashboard, and the Ollama option disappears from Browse.

If Lemonade was started with an API key (it answers `Invalid or missing API key`), set the same key as `LEMONADE_API_KEY` in the agent's Windows user environment. Keep it out of `agent.json`:

```powershell
[Environment]::SetEnvironmentVariable("LEMONADE_API_KEY", "<your key>", "User")
```

## Preview (Windows PowerShell)

```powershell
python agent/lemonade_scan.py --lemonade-url http://127.0.0.1:13305
```

This only prints what the agent would find; the agent itself uploads on every sync.

## Download and load a Hugging Face GGUF

In Browse, paste a Hugging Face repository or file link, choose **Lemonade (download & load GGUF)** and click Download on a single-file `.gguf` model. ModelHarbor asks the PC's agent to call Lemonade's `/api/v1/pull` with the exact file as the checkpoint (`owner/repo:file.gguf`, recipe `llamacpp`). Lemonade downloads the file into its own Hugging Face cache, the agent reports the download's progress, and then the agent calls `/api/v1/load`.

Models are registered under a name like `user.unsloth-Qwen3-8B-Q4_K_M`. That's the owner plus the filename, with the repository name added when the filename doesn't start with it.

If the model downloads but can't be loaded (for example, not enough memory), the job shows as failed with Lemonade's message. The model stays downloaded, and you can load it from Lemonade itself.

Limitations match the Ollama import: only single-file GGUF models are offered (no `mmproj` projectors or split shards), and repositories with duplicate filenames in different folders are rejected. For gated or private repositories, give Lemonade its own Hugging Face token (`HF_TOKEN` in the environment Lemonade runs in).

When Lemonade listens on your network, start it with an API key so other devices can't use it.

## Linking Lemonade models to Hugging Face

- **Automatic.** When Lemonade downloaded a model from Hugging Face itself, its checkpoint names the repository (`unsloth/Qwen3-0.6B-GGUF:Q4_0`). ModelHarbor shows that repo as the source ("reported by Lemonade"), and Lemonade's own `update_available` flag shows as an update. Updates happen in Lemonade, not as a download next to a file.
- **Suggested.** For local `.gguf` files (Lemonade's `extra_models_dir` or a local path), the model is listed under **Unmatched**. Suggestions search Hugging Face using the real filename from the checkpoint path, not the model's nickname. When the model's name looks like `hf.co/owner/repo:tag`, that repository is checked first, and the file named by the tag ranks above its sibling quants. Lemonade publishes no file hash, so matches are by name and size: Lemonade rounds sizes to about 0.5%, so a "same weights" match is likely but not proven, and you confirm it before linking.
- **By hand.** Search and **Link** from the model's details like any other file. Then use **Unlink from Hugging Face** to undo it, or **Mark as local only** for models that aren't on any site.

## Aliases, hardware and disk space

- **Aliases.** Lemonade often lists one file under several names (`gemma4-12b`, `extra.gemma4-12b`, `gemma4:12b`). ModelHarbor counts each file once, marks the extra names as aliases, and a link made on one name applies to all of them.
- **The Lemonade PC's hardware.** Each sync reads Lemonade's `/api/v1/system-info`, so download advice for GGUF files uses the GPU, VRAM, RAM and free disk of the machine Lemonade runs on, which may not be the agent's PC. The rating leaves about 10% plus 1.5 GB above the weights for an 8K context. The PC card on the Devices page shows that machine.
- **Disk check.** Before a pull, the agent checks Lemonade's free model storage and refuses the download if the file plus 2 GB doesn't fit.
