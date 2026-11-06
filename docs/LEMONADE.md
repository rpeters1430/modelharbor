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

## Dry run (Windows PowerShell)

```powershell
python agent/lemonade_scan.py --lemonade-url http://127.0.0.1:13305 --dry-run
```

## Download and load a Hugging Face GGUF

In Browse, paste a Hugging Face repository or file link, choose **Lemonade (download & load GGUF)** and click Download on a single-file `.gguf` model. ModelHarbor asks the PC's agent to call Lemonade's `/api/v1/pull` with the exact file as the checkpoint (`owner/repo:file.gguf`, recipe `llamacpp`). Lemonade downloads the file into its own Hugging Face cache, the agent reports the download's progress, and then the agent calls `/api/v1/load`.

Models are registered under a name like `user.unsloth-Qwen3-8B-Q4_K_M`. That's the owner plus the filename, with the repository name added when the filename doesn't start with it.

If the model downloads but can't be loaded (for example, not enough memory), the job shows as failed with Lemonade's message. The model stays downloaded, and you can load it from Lemonade itself.

Limitations match the Ollama import: only single-file GGUF models are offered (no `mmproj` projectors or split shards), and repositories with duplicate filenames in different folders are rejected. For gated or private repositories, give Lemonade its own Hugging Face token (`HF_TOKEN` in the environment Lemonade runs in).

When Lemonade listens on your network, start it with an API key so other devices can't use it.
