# Unsloth integration implementation plan

**Goal:** Connect ModelHarbor's agent to Unsloth Studio for inventory, downloads, and deletion.

**Architecture:** Extend the existing runtime inventory and job paths, with the agent authenticating directly to Unsloth. Keep credentials in the agent environment and preserve inventory on connection failures.

**Tech stack:** Python, FastAPI, SQLite, vanilla JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-unsloth-integration-design.md`

1. Verify upstream endpoint schemas, authorization, and model identity semantics; write agent contract tests for valid, invalid, and unavailable responses.
2. Add opt-in agent connection and inventory, including status and safe failure handling; test synchronization and credential handling.
3. Add queued download and deletion operations, using server-managed identities and explicit confirmation; test authorization, errors, and completion refresh.
4. Integrate the existing dashboard's runtime and browse controls, document setup and limitations, and run focused and full tests.
