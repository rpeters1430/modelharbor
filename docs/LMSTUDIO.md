# LM Studio integration

ModelHarbor can inventory local models from LM Studio, download a selected Hugging Face GGUF through LM Studio's native model manager, and queue model load or unload requests. The integration uses the native REST API v1, available in LM Studio 0.4.0 and newer, and is opt-in.

## Start the API server

Launch LM Studio and start the server from the **Developer** page, or run `lms server start`. The default endpoint is `http://127.0.0.1:1234`. ModelHarbor needs the native REST API v1; older API versions are reported as unavailable and do not replace the last inventory. See the [official REST API overview](https://lmstudio.ai/docs/developer/rest) and [server guide](https://lmstudio.ai/docs/developer/core/server).

## Enable inventory

In `agent/config.json`, enable LM Studio and set the base URL. The default is for LM Studio and the ModelHarbor Windows agent to run on the same PC:

```json
{
  "lmstudio_enabled": true,
  "lmstudio_url": "http://127.0.0.1:1234"
}
```

The API server does not require authentication by default. If you enable API authentication in LM Studio, set `LMSTUDIO_API_KEY` in the Windows user's environment. Do not put the key in the agent configuration file. Add `LMSTUDIO_API_KEY` as a user environment variable in Windows Settings, then restart the agent so it inherits the variable. The next inventory scan will report model keys, architecture, format, quantization, size, context limit, and loaded instances.

For a remote LM Studio server, use its reachable base URL and configure HTTPS and API authentication on that server. ModelHarbor only reports hardware fit estimates when the endpoint is local to the agent; remote endpoint hardware is not inferred.

## Browse and download

Open **Browse**, inspect a Hugging Face repository containing GGUF files, and choose **LM Studio (download GGUF)** as the destination. ModelHarbor enables a file only when its name contains a supported quantization and the repository contains exactly one GGUF for that quantization. This reflects the LM Studio download API, which chooses a repository file by quantization rather than by a specific file path.

The Windows agent asks LM Studio to download the model and follows its job status using the [download](https://lmstudio.ai/docs/developer/rest/download) and [status](https://lmstudio.ai/docs/developer/rest/download-status) endpoints. ModelHarbor does not download or store the GGUF itself, so the completed download is provider-reported and is not SHA-256 verified by ModelHarbor.

## Load and unload

Open an LM Studio model from the library. The integration uses LM Studio’s documented [load](https://lmstudio.ai/docs/developer/rest/load) and [unload](https://lmstudio.ai/docs/developer/rest/unload) endpoints. The details panel shows runtime metadata and loaded instances. Choose a context length up to LM Studio's reported model limit, then select **Load in LM Studio**. Use **Unload** beside a loaded instance to unload it. Requests run through the Windows agent; LM Studio must be running and its API reachable.

## Troubleshooting

- **Connection refused:** start the LM Studio server and check the configured URL and port.
- **401 or 403:** confirm API authentication is enabled as expected and that `LMSTUDIO_API_KEY` matches the token generated in LM Studio.
- **API unavailable:** ModelHarbor requires the native `/api/v1` endpoints. Update LM Studio if the server only exposes an older API; the last inventory remains available while it is offline.
- **File unavailable in Browse:** ModelHarbor needs a `.gguf` filename with a supported quantization, and there must be only one repository file for that quantization.

## Notes

- LM Studio models do not expose local file hashes through this inventory API, so exact hash matching and hash-based update checks do not apply.
- Load and unload requests do not change model files. Downloads are managed by LM Studio and stored in its configured model directory.
- Disable `lmstudio_enabled` to remove LM Studio from future inventory scans. If the option is absent from an older config, the integration remains disabled.
- The ModelHarbor dashboard API token still protects the queued actions. Keep dashboard access behind HTTPS when accessed remotely.
