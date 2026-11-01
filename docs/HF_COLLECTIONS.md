# Hugging Face repository collections

ModelHarbor can browse individual model files inside a public Hugging Face repository.

Example: `pmczip/MOSS_Image_LoRAs` is one **repository** containing many individual LoRA files. ModelHarbor lists each supported model file separately, with its relative path, reported file size, and SHA-256 **when the Hub exposes it through LFS metadata**.

## How to use

1. Open any installed model in the mobile dashboard.
2. Scroll to **Browse Hugging Face collection**.
3. Enter `pmczip/MOSS_Image_LoRAs`, then press **Browse files**.
4. Use **Find a file in this collection** to narrow filenames, including files in subfolders.
5. Open **View file** to inspect the individual Hugging Face file page.

Hugging Face search results also offer **Browse repository files**.

## Important limitations

- Local comparisons currently use filename and file size only. They are **candidate matches, not verified installed models**.
- Remote SHA-256 may be exposed, but local hashes are not yet stored or computed; no exact match can be claimed.
- Only public, accessible repositories work without credentials. Private or gated repositories may fail.
- Listings are capped at 10 pages / 9,000 entries to protect a small NAS. A partial listing is explicitly labeled; narrow searches inside a partial listing may miss files.
- Files are displayed from a **read-only metadata request**; no model binaries are downloaded, moved, or changed.
- For Hugging Face model repositories only; datasets and Spaces are outside this initial scope.

## Endpoint

`GET /api/huggingface/repository?repo=owner/name&q=optional`

Requires the standard `X-ModelHarbor-Token` header. Responses include `files`, `folders`, `complete`, and `scanned_entries`.

The backend contacts only the official Hugging Face API with fixed URLs. Pagination does not follow arbitrary external links.

Next milestone: local opt-in SHA-256 hashing, verified per-file matches, and version monitoring with user approval.
