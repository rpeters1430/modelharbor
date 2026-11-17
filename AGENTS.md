# Development Instructions for Coding Agents

## Before Making Changes
1. **Read the documentation first**: `README.md`, `docs/*.md`, `CLAUDE.md`. Understand the architecture and conventions.
2. **Verify the working tree is clean**: `git status` should show no uncommitted changes.
3. **Identify the exact problem** before writing code. Keep the scope minimal.

## Python Execution
- Use `/projects/modelharbor/.venv/bin/python` for all Python execution and tests.
- Never repeatedly install dependencies. If a required dependency is missing, **report it** instead of silently installing.
- Always inspect `git diff` before committing.

## Testing
- Run relevant tests **after every implementation change**. Report actual results, not assumptions.
- Never claim a command or test succeeded without executing it.
- If the same failure persists after two reasonable fixes, **stop and report the blocker**.

## Security
- Preserve download integrity, SHA-256 verification, path validation, authentication, and other security checks.
- Never commit tokens, credentials, virtual environments, caches, generated secrets, or `.venv` directories.

## Git Workflow
- Always create a `feature/` or `fix/` branch. **Never push directly to main**.
- Keep changes focused; avoid unrelated refactoring.
- Create **draft pull requests only**. Never merge automatically.
