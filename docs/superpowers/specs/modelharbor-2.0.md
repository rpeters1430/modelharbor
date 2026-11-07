# ModelHarbor 2.0 — Product Design

**Status:** Draft for review  
**Branch:** `docs/modelharbor-2.0-roadmap`  
**Repository baseline:** `main` at commit `1816589fab0c2ea557d1374733753ca858c64c4a` (2026-11-06)

## Intent

Make ModelHarbor the self-hosted control center for models across the user's PCs and model runtimes. The 2.0 release should help a user answer three practical questions: what models do I have, where can I run them, and what do I need to install to reproduce a useful setup on another machine?

## Current state

The repository already supports ComfyUI inventory, Ollama and Lemonade inventory/operations, source browsing for Civitai, Hugging Face and ModelScope, hash-aware identification and update checks, verified downloads, manual model links, conversion jobs, device-specific fit advice, and an active download tray. Recent commits also add Lemonade's hardware and disk reporting, hash-based duplicate recognition, and separate background worker lanes for long-running jobs.

Those capabilities are spread across detailed docs, while the top-level README still describes several as future work or conditional. Documentation accuracy is part of the 2.0 work.

## Goals

1. Add LM Studio as the next first-class runtime, using the Windows agent to communicate with the runtime on its host.
2. Let users save named model sets and see whether each device/runtime has the required compatible models.
3. Improve LLM fit guidance with explicit context-size and runtime assumptions; show an estimate and its basis, and return “unknown” when metadata is insufficient.
4. Turn duplicate recognition into useful storage insight across devices and roots, while preserving the existing hash-based safety.
5. Make long-running jobs easier to recover and control, building on the existing SQLite jobs and worker lanes.
6. Refresh the README and setup docs so they match shipped behavior and define what “ModelHarbor 2.0” covers.

## Non-goals

- Replacing ComfyUI Manager, Stability Matrix, Ollama, Lemonade, or LM Studio.
- Moving model files into a new central storage scheme.
- Automatically installing or deleting a model set without a user action.
- Claiming exact runtime compatibility when the model architecture or required metadata is unknown.
- Adding cloud inference or uploading model files to a hosted service.

## Proposed product behavior

### Runtime coverage: LM Studio

The Windows agent should query an explicitly configured LM Studio endpoint, inventory local/downloaded models, and report runtime identity, status, model path or stable model identifier, size when available, and hardware for the LM Studio host. Browse should offer supported GGUF downloads to LM Studio. Where the official LM Studio API supports it, the user may request load or unload operations and see the runtime's reported result.

Use the LM Studio native REST API for management operations. Keep API credentials on the agent host; do not send provider credentials to the NAS. Do not make model serving or inference requests through ModelHarbor.

### Model sets

Users can create named sets from known model identities (provider/repository/file where available, or local-only identity). A set has an optional purpose and per-item runtime/format requirements. The dashboard should show installed, missing, incompatible, and unknown items per device/runtime. A user-triggered “prepare this set” action may queue only the supported downloads for a selected target. It must show destination, estimated bytes, and unresolved requirements before queuing. It must never remove unrelated or old models to make room.

### Fit guidance

For LLMs, let the user select a target context length or use a clearly labeled default. Estimate weights plus KV-cache and runtime overhead only when architecture metadata permits a defensible estimate. Show the assumptions, distinguish VRAM fit from RAM offload, and include free disk. For incomplete metadata, show “not enough information” with the missing fields instead of a confident pass/fail. Keep image-model advice separate from LLM advice.

### Duplicate and storage insight

Group files by verified SHA-256 where available, across devices and configured roots. Show physical copies, aliases/hardlinks, and storage potentially reclaimable as separate quantities. The first release should be report-first. Any future remove or relink action must revalidate identity immediately before acting, show every affected path/device, and require confirmation; it must never infer that a duplicate is safe to delete merely from matching names or sizes.

### Job recovery

Build on the existing database-backed jobs and worker lanes. Preserve job state across dashboard reloads and container/agent restarts, show truthful progress and last update, and offer retry or cancel only when the underlying operation can stop safely. Pause/resume should be offered only for operations and providers that actually support it. A stale job must be reconciled with the agent/runtime before a retry so the same large transfer is not started twice.

## Approaches considered

1. **Incremental runtime adapter plus model sets (recommended).** Add LM Studio using a narrow adapter shaped by existing Lemonade/Ollama behavior, then add model sets and the fit/storage views over the common inventory. This fits the current agent-plus-NAS architecture and lets each milestone ship independently.
2. **Build a general plugin framework for every runtime first.** More extensible in theory, but adds abstraction before a third/fourth integration proves which parts are genuinely common.
3. **Focus only on model recommendation and optimization.** Lower integration cost, but leaves ModelHarbor unable to manage a major local LLM runtime used by the target audience.

**Decision:** Use approach 1. Keep provider-specific operations behind runtime adapters and retain the NAS as the inventory, set, and job coordination service.

## Milestones

### 2.0 foundation — LM Studio connector

- Define a provider-neutral runtime inventory/capability contract based on existing ComfyUI, Ollama, and Lemonade records.
- Add LM Studio scanning and secure endpoint configuration to the agent.
- Add supported download/load/unload operations and job reporting through LM Studio's official API.
- Add device setup and troubleshooting documentation.

### 2.0 model sets

- Add persisted sets and set items with stable model references.
- Add per-device/runtime readiness and an explainable missing/incompatible/unknown state.
- Add a reviewed, user-triggered prepare action that uses existing verified download jobs.

### 2.0 intelligence and operations

- Add context-aware LLM memory estimates with explicit unknown states.
- Add fleet duplicate/storage reporting, including copy and alias distinctions.
- Improve long-job recovery and safe controls where provider APIs support them.

### Release-wide

- Correct README, roadmap, and setup docs.
- Preserve the single-container NAS deployment and Windows-agent model.
- Keep downloads and any cleanup actions explicit and verifiable.

## Acceptance criteria

- A configured LM Studio server appears as its own runtime on the correct host and does not overwrite ComfyUI, Ollama, or Lemonade inventory.
- LM Studio downloads and load/unload requests display runtime-confirmed status and errors; unsupported formats/actions are rejected before a job is created.
- A saved model set displays per-target readiness and never reports unknown compatibility as a pass.
- Context-aware fit output includes its context and metadata assumptions; missing architecture information produces an unknown result.
- Duplicate reports distinguish byte-identical physical copies from aliases/hardlinks and do not count storage savings twice.
- Job state survives client reload and service restart; retries reconcile current runtime/agent state first.
- No model is deleted, overwritten, moved, or installed as a side effect of scanning or viewing a set.
- README feature lists, security notes, and roadmap match the shipped behavior.

## Constraints and risks

- LM Studio's REST API is versioned and may expose different management capabilities across releases. The connector must detect capability/version and disable unsupported actions rather than guess.
- Inventory includes models from several runtimes whose identities and local paths differ. A common model identity must not erase provider-specific metadata.
- Memory estimates are advisory. Model architecture, KV-cache precision, GPU split, and runtime overhead can change the result; surface uncertainty.
- Cross-device deduplication does not imply files can be hardlinked or safely removed across filesystems.
- Large model operations may outlast agent connectivity. Job reconciliation must be idempotent and must not duplicate a download.
- Keep provider tokens on the agent/runtime host; protect the existing NAS token and avoid logging secrets.

## Open design points for implementation planning

- LM Studio endpoint discovery: explicit URL only for the first version, or optional LAN discovery?
- Model set ownership: one shared NAS library, or per-user sets if multi-user auth is introduced?
- For 2.0, should storage insight remain report-only, or include a separate opt-in cleanup action after review?
