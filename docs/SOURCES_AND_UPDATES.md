# Source identification and update checks

ModelHarbor identifies models by **exact file hash**, not by filename, and checks the source for newer versions. Nothing is downloaded, replaced or deleted.

## How it works

1. **Windows agent:** during each inventory sync it computes the SHA-256 of every ComfyUI model file and sends it with the inventory. Hashes are cached in `%APPDATA%\ModelHarbor\hashes.json` and recomputed only when a file's size or modified time changes. Hard links are hashed once. The first sync reads the whole library (roughly 10–15 minutes per 750 GB on NVMe). Set `"hash_models": false` in `agent.json` to turn this off.
2. **NAS background checker** (every 5 minutes, bounded and rate-limited):
   - **Civitai:** each new hash is looked up with `/api/v1/model-versions/by-hash/{sha256}` on `civitai.com`, falling back to `civitai.red`. A hit is an exact match: model, version, base model and trigger words are stored. Hashes Civitai doesn't know are retried weekly.
   - **Civitai updates:** once a day per model, `/api/v1/models/{id}` is read and the newest published version **for the same base model** is compared by publish date. A version for another base model (e.g. an Illustrious variant of a Pony LoRA) is not reported as an update.
   - **Ollama:** once a day per model, the registry manifest is fetched from `registry.ollama.ai`. Its SHA-256 is the digest Ollama reports locally, so a different value means a newer build. Models pulled from other registries are skipped.
   - **Hugging Face:** matches you selected after verification are rechecked daily; a changed SHA-256 on `main` is reported as an update.
3. **Dashboard:** rows show `✓ Civitai` / `⬆ Update`, the details dialog shows the source, base model, trigger words and links, and the library has an **Updates available only** filter. Mature models link to `civitai.red`.

Hugging Face verification requests now resolve instantly from the inventory hash; the hash worker is only needed for files the agent could not hash.

Set `MODELHARBOR_SOURCE_CHECKS=0` on the container to turn off all outbound checks.
