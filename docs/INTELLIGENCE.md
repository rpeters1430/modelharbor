# Inventory intelligence (first pass)

This milestone enriches existing model inventory at read time. It does **not** alter the database, scan local files, or contact external providers.

- **Physical devices:** group `windows-pc:comfyui` and `windows-pc:ollama` under `windows-pc`.
- **Possible duplicates:** same case-insensitive filename and reported size across ComfyUI inventory entries. These are **candidates only**. A matching SHA-256 digest would be required to establish identical bytes. No delete action is offered.
- **Family guesses:** conservative filename heuristics for Pony, SDXL, Illustrious, FLUX, Wan, Qwen, and SD 1.5. Guesses are explicitly marked unverified.
- **Library:** sorting and filtering by family and possible duplicates.

## Next steps

Add optional local file hashes and provider IDs; then implement authenticated Hugging Face/Civitai metadata lookup with caching, rate limits, and manual confirmation of ambiguous matches. Only after source identities are verified should the UI offer update checks. No model download/update actions are included in this milestone.
