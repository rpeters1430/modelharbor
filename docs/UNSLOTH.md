# Unsloth Studio integration

ModelHarbor's PC agent can connect to Unsloth Desktop or Studio to inventory cached repositories, request selected GGUF downloads, and delete a specifically identified cached copy. Enable the Unsloth server and create a **full-access** API key in Unsloth Studio under Settings > API. An inference-only key can list loaded models but Unsloth refuses it for downloads and deletions. In the agent configuration, set:

```json
{
  "unsloth_enabled": true,
  "unsloth_url": "http://127.0.0.1:8888"
}
```

Set `UNSLOTH_API_KEY` in the agent's environment and restart the agent. Do not put the key in the configuration. On Linux, add it to `~/.config/ModelHarbor/agent.env`; on Windows, set it as a user environment variable before installing the scheduled task. For a remote server, configure its LAN access and use an encrypted connection. The Unsloth server must remain reachable by the agent.

Gated Hugging Face repositories need a Hugging Face token. Unsloth does not lend its own stored token to API-key callers, so set `UNSLOTH_HF_TOKEN` in the agent's environment as well; the agent sends it to Unsloth in the `X-Unsloth-HF-Token` header.

On the next scan, cached GGUF and other cached repositories appear under the Unsloth device. Partly downloaded repositories are left out until they finish, and an entry Unsloth reports in an unexpected form is skipped and logged rather than hiding the whole list. If the server is unavailable, the previous inventory is preserved and hidden until it reconnects.

Browse can request a single GGUF file. The agent asks Unsloth to map it to an unambiguous variant, follows Unsloth's download status and shows the bytes downloaded so far. If Unsloth stops the download, the job shows Unsloth's own reason. The server owns and verifies its download; ModelHarbor does not independently verify its hash. ModelHarbor's file-format and hardware-fit indications are estimates, not proof that a server version supports the model architecture.

Deleting a repository needs the cache reference Unsloth reports for that copy. When the server does not identify the copy (for example, for non-GGUF repositories), ModelHarbor directs you to delete it in Unsloth Studio instead. Cached repositories can be shared with other applications. Deletion may affect those applications and removes the whole repository copy, not just one GGUF variant.

The integration uses Unsloth's `GET /api/models/cached-gguf`, `GET /api/models/cached-models`, `GET /api/models/gguf-variants`, `POST /api/hub/download`, `GET /api/hub/download-status`, `GET /api/hub/gguf-download-progress` and `DELETE /api/hub/delete-cached` endpoints. Older versions or restricted keys may not provide these routes or permissions. If a model download or deletion fails, check the job error, server version, API key permissions, and the Unsloth server logs.
