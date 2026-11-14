# Remove Offline Devices Implementation Plan

**Goal:** Remove stale devices and their inventory from the dashboard.
**Architecture:** Authenticated transactional SQLite cleanup, invoked by an offline-only confirmed dashboard action.
**Tech Stack:** FastAPI, SQLite, vanilla JavaScript.
**Spec:** docs/superpowers/specs/2026-11-13-remove-device-design.md

## Constraints and review focus
Preserve other devices and shared metadata; reject online devices, including recent workers; require authentication; permit later registration. Do not queue file deletion.

### Task 1: API and dashboard
- [x] Add tests for full cleanup, exact name isolation, online/worker rejection, authentication, missing devices, and reconnection.
- [x] Run tests and confirm failure before implementation.
- [x] Implement DELETE /api/agents/{device} and offline-only confirmation button, refreshing inventory after removal.
- [x] Update README; run full pytest, JavaScript syntax check and diff check.
- [ ] Commit, push feature branch and open PR.

Verification: new API tests failed with missing endpoint, then passed. Full suite: 140 passed (two dependency deprecation warnings). JavaScript syntax and git diff checks passed. Self-review confirmed exact source matching, transactional online check, and no file deletion jobs.
