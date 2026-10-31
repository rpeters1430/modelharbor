# Provider discovery (manual, read-only)

The ModelHarbor dashboard can now search **Hugging Face** and **Civitai** via server-side API calls. Search is explicitly initiated from the model details dialog. Results are **candidates**, not verified matches to the local file.

## Safety
- Requests are sent only to hardcoded official provider API hosts, not arbitrary user URLs.
- Search query is limited to 3–120 characters.
- API responses are bounded and cached for 15 minutes in memory.
- No provider API keys are required for public search, but provider rate limits and access restrictions apply.
- Search results never trigger model downloads, updates, deletion, or modifications.
- External provider links open in a new browser tab.
- Civitai may include adult-oriented model names; no images are loaded or proxied in this initial milestone.

## Planned next step

Persist user-confirmed provider/model/version identifiers in SQLite, collect locally computed hashes when explicitly requested, and match against authoritative version-file hashes. Only then provide update eligibility indicators. Avoid equating fuzzy filename similarity with verified identity.
