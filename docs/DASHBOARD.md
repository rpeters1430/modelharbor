# ModelHarbor responsive dashboard

The web UI is served by the existing FastAPI service at `http://NAS-IP:8096/`. No additional container, Node runtime, or external CDN is required.

## Getting started
1. Deploy with `docker compose up -d --build` on the NAS.
2. Visit the NAS address in a browser.
3. Enter the same `MODELHARBOR_AGENT_TOKEN` configured in your Compose environment.
4. Run the ComfyUI scanner and (after PR #3 is merged) the Ollama scanner to populate inventory.

## Mobile
The dashboard adapts to narrow screens, provides touch-friendly navigation, and includes a web app manifest. Full installability as a PWA may require HTTPS and proper icons in a later release. For remote access, use Tailscale and a trusted HTTPS reverse proxy.

## Security and scope
The dashboard stores the API token in browser session storage; it is cleared when the browser session ends. Avoid shared browsers. The API and dashboard should only be exposed to trusted LAN/Tailscale clients. HTTP alone does not encrypt the token. No third-party scripts are loaded. This release is read-only: discovery, update checks, downloads, and automatic refresh are not implemented.
