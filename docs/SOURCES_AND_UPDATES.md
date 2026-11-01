# Source identification and update checks

ModelHarbor identifies models by **exact file hash**, not by filename, and checks the source for newer versions. Nothing is downloaded, replaced or deleted.

## How it works

1. **Windows agent:** during each inventory sync it computes the SHA-256 of every ComfyUI model file and sends it with the inventory. Hashes are cached in `%APPDATA%\ModelHarbor\hashes.json` and recomputed only when a file's size or modified time changes. Hard links are hashed once. The first sync reads the whole library (roughly 10–15 minutes per 750 GB on NVMe). Set `"hash_models": false` in `agent.json` to turn this off.
2. **NAS background checker** (every 5 minutes, bounded and rate-limited):
   - **Civitai:** each new hash is looked up with `/api/v1/model-versions/by-hash/{sha256}` on `civitai.com`, falling back to `civitai.red`. A hit is an exact match: model, version, base model and trigger words are stored. Hashes Civitai doesn't know are retried weekly.
   - **Civitai updates:** once a day per model, `/api/v1/models/{id}` is read. An update is a version with the **same name apart from a higher version number** (`Turbo v1.0 → Turbo v3.0`), for the same base model, that contains the **same kind of file** you have (main weights, text encoder, VAE…). Inpainting, DMD, quantized, other-base versions, and files Civitai can't place are not reported.
   - **Ollama:** once a day per model, the registry manifest is fetched from `registry.ollama.ai`. Its SHA-256 is the digest Ollama reports locally, so a different value means a newer build. Models pulled from other registries are skipped.
   - **Hugging Face:** matches you selected after verification are rechecked daily; a changed SHA-256 on `main` is reported as an update.
3. **Dashboard:** rows show `✓ Civitai` / `⬆ Update`, the details dialog shows the source, base model, trigger words and links, and the library has an **Updates available only** filter. Mature models link to `civitai.red`.

Hugging Face verification requests now resolve instantly from the inventory hash; the hash worker is only needed for files the agent could not hash.

Set `MODELHARBOR_SOURCE_CHECKS=0` on the container to turn off all outbound checks.

## Downloading updates

Open a model with **⬆ Update** in the dashboard and press **Download update**. Nothing downloads without that press.

- The NAS decides exactly which file to fetch and the SHA-256 it must have: for Civitai, the file of the same kind (main weights, text encoder, VAE…) in the newer version; for Hugging Face, the changed file on `main`; for Ollama, the newer build of the same tag.
- The Windows agent's background task (`ModelHarbor Hash Worker`) downloads it **next to the old file**, only from `civitai.com`, `civitai.red` or `huggingface.co`, after checking free disk space. The file is written as `.partial`, its SHA-256 is checked, and only then is it renamed. A mismatch deletes the partial file.
- It never overwrites or deletes anything. An existing file with the same name stops the download. Hugging Face copies get a hash suffix (`name.1a2b3c4d.safetensors`) because the upstream filename is unchanged.
- Ollama updates run `ollama pull` for the same tag through the local Ollama API.
- The new file appears in the dashboard after the next inventory sync. Remove the old version yourself once you're happy with the new one.

### API keys (only on the Windows PC)

Some downloads require an account. Store keys in your Windows **user** environment, like the ModelHarbor token. They are never sent to the NAS.

```powershell
[Environment]::SetEnvironmentVariable("CIVITAI_API_TOKEN", "your-civitai-key", "User")  # civitai.com > Account settings > API Keys
[Environment]::SetEnvironmentVariable("HF_TOKEN", "hf_...", "User")                     # only for gated or private repos
```

Then restart the worker so it picks them up: `Stop-ScheduledTask "ModelHarbor Hash Worker"; Start-ScheduledTask "ModelHarbor Hash Worker"`. Hugging Face uses tokens, not browser cookies.

Set `"downloads_enabled": false` in `agent.json` to turn downloads off on that PC.

## Linking a model by hand

For a file the hash lookup didn't find, open it, search Civitai under **Find model online**, and press **Link to my file** on the right result. Pick the version and file you have: **✓** marks a file whose SHA-256 is identical to yours, which links as an exact match. Linking to a different file asks first and is shown as "linked by hand, hash differs". Update checks and downloads work either way, and downloads are still verified against the new file's published hash. **Unlink** removes the link, and the file is looked up by hash again.
