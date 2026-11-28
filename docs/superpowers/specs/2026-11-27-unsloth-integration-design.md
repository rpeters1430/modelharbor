# Unsloth Studio integration design

## Goal

Let ModelHarbor connect to an Unsloth Desktop or Studio server and show, download, and delete its models through the existing dashboard and agent job system.

## Approach

Use the existing PC agent as the client, consistent with the LM Studio integration. Configure an opt-in server URL and keep the API key in an environment variable. Inventory belongs to a distinct Unsloth device and an unavailable server must not erase the last good inventory. Queue downloads and deletions on the agent, report errors and progress, and refresh inventory after successful operations. Preserve the existing dashboard's interaction and confirmation patterns.

Connecting directly from the backend would bypass the established agent architecture and may not reach a desktop server. Scanning and modifying the Hugging Face cache directly would not reliably represent remote servers or Unsloth's own model management, and can affect files shared with other applications. Prefer supported server operations and clearly report any operation the installed server or its credentials do not support; do not silently fall back to filesystem deletion.

## API and safety

Unsloth documents authenticated MCP `list_models`, `download_model`, and `get_job` operations. Confirm the installed version's exact schemas and its supported deletion endpoint and authorization before implementing deletion. Avoid claiming deletion works when only an inference-only key or an unsupported server version is available. Never expose the API key to the dashboard, logs, or inventory. Validate endpoints and model identifiers, preserve the existing dashboard authorization and explicit delete confirmation, and handle offline, authentication, unsupported-version, malformed-response, and failed-job states.

## Validation and documentation

Add focused tests covering inventory mapping, connection failures, authentication, download and delete requests and failures, agent job handling, and backend authorization. Run the relevant tests and full suite where feasible. Document setup, server permissions, supported versions and limitations, and shared-cache implications. Verify against a live Unsloth server if one is available; otherwise explicitly report that limitation.
