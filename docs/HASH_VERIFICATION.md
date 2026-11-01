# Exact Hugging Face file matching with SHA-256

ModelHarbor can verify a **specific installed ComfyUI model file** against a Hugging Face repository file using SHA-256. The NAS never reads or downloads the local model binaries. Only the Windows worker reads the selected local file.

## How it works

1. Open a model in ModelHarbor, then browse the Hugging Face repository containing the file.
2. Choose an online file with a published LFS SHA-256 and an exact byte-size match.
3. Choose one of the local ComfyUI model files shown in the dropdown. Matching filenames are listed first; files with different names but identical sizes are also eligible.
4. Press **Verify SHA-256**. This explicitly queues a one-file verification request.
5. A separate, lightweight Windows background worker polls the NAS around every 20 seconds and reads that file in 4 MB chunks. It never changes the file.
6. When the digest matches the provider's published SHA-256, ModelHarbor displays **Select verified match**. This second action saves your confirmed file-to-repository association.
7. A verified selection appears as a green label in the repository browser.

**Important:** A filename match, size match, or provider search hit is *not* a verified model. A byte-for-byte SHA-256 match is required before the selection button is enabled. If a file changes during hashing, the worker refuses the check. Changes in the inventory or remote published SHA invalidate the selection action.

## Upgrade instructions for Windows

This update requires **both the new NAS container and the updated Windows agent scripts**.

- Merge the PR after checks pass. Pull and redeploy ModelHarbor through Dockhand on your NAS.
- Update the local Windows agent checkout containing `modelharbor_agent.py`, `hash_worker.py`, and `install-task.ps1`.
- Run `powershell -ExecutionPolicy Bypass -File agent/install-task.ps1` from the repository root while signed into your usual Windows account. This preserves the inventory sync schedule and installs/starts a second scheduled task, **ModelHarbor Hash Worker**.
- Verify the second task is running using Windows Task Scheduler.
- Your existing `%APPDATA%\ModelHarbor\agent.json` and environment token are preserved.

Optional agent.json settings (defaults shown):

```json
{
  "hash_verification_enabled": true,
  "hash_poll_seconds": 20
}
```

The hash worker may be disabled by setting `hash_verification_enabled` to `false`.

## Limits and security

- One explicit job per selected file. No bulk hashing of the entire 1 TB library.
- Jobs are queued in SQLite and the worker only accepts files inside its configured ComfyUI model roots. Files outside those roots and symbolic links are rejected. Hardlinks within roots can be verified normally.
- SHA-256 verification needs both authoritative SHA-256 metadata and exact byte size from the Hugging Face repository tree. When remote metadata lacks a SHA-256, verification is unavailable.
- The server stores the verified association and selected hash, not a copy of the model file.
- The SHA verification does not install, download, rename or delete models.
- Selection is tied to the inventory record and its scanned size/mtime. A subsequent modification may require re-verification. Version update monitoring is a future feature.
- Hashing large checkpoints can take some time and cause disk I/O on Windows; it runs only when requested.
- All API operations require `X-ModelHarbor-Token`. Use your trusted LAN or HTTPS/Tailscale.

### API routes

`POST /api/hash-jobs`: Queue verification using a device, local path, repository, and remote file path.

`GET /api/hash-jobs/next?device=...`: Worker claims a queued job.

`POST /api/hash-jobs/{id}/result`: Worker reports SHA-256 or a safe error.

`GET /api/hash-jobs/{id}`: Check status.

`POST /api/hash-jobs/{id}/select`: Explicitly associate **only** a verified result.

`GET /api/selected-matches`: List persisted associations.
