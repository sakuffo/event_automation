# Architecture Audit (2025-11-01)

This snapshot reflects the post-modularisation state of the automation project.

## Code Inventory

- `sync_events.py` – Thin compatibility wrapper that forwards to `event_sync.cli.main()`.
- `event_sync/` – Modular package containing CLI, config, runtime, Sheets/Drive helpers, image handling, typed models, and orchestration.
- `wix_client.py` – Reusable REST client with retry + media upload utilities leveraged by the package and dev scripts.
- `tests/` – Pytest suite covering models, image compression, and CLI error handling (executed locally via `make unit` and in CI via `ci.yml`).
- `dev_events.py`, `dev_tickets.py`, `inspect_tickets.py` – Operator tooling built on `WixClient`; still use print-style output and can adopt shared logging in future iterations.

## Current Strengths

1. **Modular boundaries** – Core sync functionality lives in importable modules, enabling re-use and testing.
2. **Central config** – `AppConfig` validates required environment variables and surfaces clear errors.
3. **Structured logging** – CLI + orchestrator log through `logging`, with `--log-level` available on every command.
4. **Automated tests** – Unit tests exercise data validation, image compression, and command-line failure paths; CI runs on every push/PR.
5. **Image resilience** – `prepare_image_for_wix()` automatically compresses oversized Drive assets before upload.

## Remaining Opportunities

1. **Dev tooling parity** – Migrate `dev_events.py` / `dev_tickets.py` to the shared logging helpers for consistent output.
2. **End-to-end dry run** – Add a mocked integration test that exercises the full sync orchestration without external calls.
3. **Credential scaffolding** – Generate an `.env.example` from `AppConfig` to reduce onboarding errors.
4. **Observability** – Consider optional structured JSON logging or lightweight error notifications for production runs.

## Documentation Status

| Doc | Status | Notes |
| --- | --- | --- |
| `README.md` | ✅ Current | Describes modular package, testing, and logging options.
| `docs/CODE_AUDIT.md` | ✅ Current | Mirrors this audit and tracks hardening progress.
| `docs/DEV_TOOLS.md` | ✅ Updated | Includes logging guidance and dev/test workflow pointers.
| `docs/HISTORY.md` | ✅ Current | Change log intact.
