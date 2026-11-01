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
- The Windows agent's background task (`ModelHarbor Hash Worker`) downloads it **next to the old file**, only from `civitai.com`, `civitai.red`, `huggingface.co` or `modelscope.cn`, after checking free disk space. The file is written as `.partial`, its SHA-256 is checked, and only then is it renamed. A mismatch deletes the partial file.
- A download never overwrites anything. An existing file with the same name stops the download. Hugging Face copies get a hash suffix (`name.1a2b3c4d.safetensors`) because the upstream filename is unchanged.
- Ollama updates run `ollama pull` for the same tag through the local Ollama API.
- The new file appears in the dashboard after the next inventory sync.
- Once the download has finished and passed its hash check, **Delete old version** appears. After you confirm, the agent re-hashes both files and permanently deletes the old one only if the new file still matches its verified hash and the old file is exactly the one in the inventory. Otherwise nothing is deleted and the reason is shown. A rescan runs afterwards so the dashboard updates. Ollama needs no cleanup, because a pull replaces the old build.

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

## Browsing and downloading new models

The **Browse** tab searches Civitai (filter by type, base model and sort, with Load more) or Hugging Face (filter by image models, image LoRAs, image editing, text-to-video, image-to-video or video LoRAs; sort by trending, downloads, likes or newest). Leave the search empty on any site to list the top models for the filters.
- **ModelScope** (Alibaba's model hub, with official Qwen, Wan and Z-Image releases and many ComfyUI repackages) is browsed like Hugging Face: filter by image models, image editing, text-to-video or image-to-video, sort by downloads, likes or recently updated, with Load more. It has no LoRA filter, so search "lora". Files are matched by their published SHA-256 and downloaded from `modelscope.cn` only; no key is needed for public models. ModelScope is used for browsing and downloading only, not for identifying existing files or update checks.

- Every file is compared with your inventory by **SHA-256**: **✓ On your PC** means you already have exactly that file. "Other version on your PC" means you have a different version of the model.
- **Download** queues the file for your PC. The NAS takes the URL, file name and expected hash from the provider, never from the browser. The agent saves it into a fixed ComfyUI folder under your first configured models root and keeps it only if the hash matches:
  - Civitai: by file type, then model type. LoRA/LyCORIS → `loras`, Checkpoint → `checkpoints`, UNet/Diffusion Model → `diffusion_models`, Text Encoder → `text_encoders`, VAE → `vae`, Embedding → `embeddings`, ControlNet → `controlnet`, Upscaler → `upscale_models`.
  - Hugging Face: the folder you pick on the card (default `loras`).
- A finished download requests a rescan, so the card and library show it as on your PC.
- With more than one PC connected, a **Download to** picker chooses which one.
- Mature preview images are hidden unless **Show mature previews** is ticked. That setting is remembered per browser.

## Unmatched files

The overview's **N to match →** (under "files identified") opens the library filtered to **Unmatched only**: hashed ComfyUI files no site has identified yet. For each one, open it and either:

- search under **Find model online** and use **Link to my file** (Civitai) or **Browse Hugging Face collection** to match it, or
- press **Mark as local only** for your own LoRAs and merges that are on no site. They are tagged "Local only" and leave the list; **Show in Unmatched again** undoes it.

Links and local-only marks are stored by the file's SHA-256, so they survive rescans, renames and moves.
