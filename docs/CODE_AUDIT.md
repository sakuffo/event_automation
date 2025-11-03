# Architecture Overview & Hardening Checklist (2025-11-01)

This supersedes the early duplication audit. The project now ships as a small package (`event_sync/`) with clear module boundaries, automated tests, and converged documentation.

## Architecture Snapshot (2025-11-01)

### Code Inventory

- `sync_events.py` – Thin compatibility wrapper that forwards to `event_sync.cli.main()`.
- `event_sync/` – Modular package containing CLI, config, runtime, Sheets/Drive helpers, image handling, typed models, and orchestration.
- `wix_client.py` – Reusable REST client with retry + media upload utilities leveraged by the package and dev scripts.
- `tests/` – Pytest suite covering models, image compression, and CLI error handling (executed locally via `make unit` and in CI via `ci.yml`).
- `dev_events.py`, `dev_tickets.py`, `inspect_tickets.py` – Operator tooling built on `WixClient`; still use print-style output and can adopt shared logging in future iterations.

### Current Strengths

1. **Modular boundaries** – Core sync functionality lives in importable modules, enabling re-use and testing.
2. **Central config** – `AppConfig` validates required environment variables and surfaces clear errors.
3. **Structured logging** – CLI + orchestrator log through `logging`, with `--log-level` available on every command.
4. **Automated tests** – Unit tests exercise data validation, image compression, and command-line failure paths; CI runs on every push/PR.
5. **Image resilience** – `prepare_image_for_wix()` automatically compresses oversized Drive assets before upload.

### Ongoing Opportunities

1. **Dev tooling parity** – Migrate `dev_events.py` / `dev_tickets.py` to the shared logging helpers for consistent output.
2. **End-to-end dry run** – Add a mocked integration test that exercises the full sync orchestration without external calls.
3. **Credential scaffolding** – Generate an `.env.example` from `AppConfig` to reduce onboarding errors.
4. **Observability** – Consider optional structured JSON logging or lightweight error notifications for production runs.

### Documentation Status

| Doc | Status | Notes |
| --- | --- | --- |
| `README.md` | ✅ Current | Describes modular package, testing, and logging options.
| `SETUP.md` | ✅ Current | Setup workflow matches CLI and Make targets.
| `docs/CODE_AUDIT.md` | ✅ Current | Mirrors this audit and tracks hardening progress.
| `docs/DEV_TOOLS.md` | ✅ Updated | Includes logging guidance, dev/test workflows, and regression checklist.
| `docs/TICKETING.md` | ✅ Current | Documents REST nuances and ticket automation controls.
| `docs/HISTORY.md` | ✅ Current | Consolidated change log.

## Module Map

| Module / Script | Responsibility | Notes |
| --- | --- | --- |
| `event_sync/cli.py` | Argparse CLI with `--log-level` support. | Invoked by the compatibility wrapper `sync_events.py`. |
| `event_sync/config.py` | Loads and validates env configuration. | Raises `ConfigError` if required values are missing. |
| `event_sync/runtime.py` | Lazy Google/Wix client factory + caches. | Shared by orchestration/tests. |
| `event_sync/sheets.py` | Reads Sheet rows, maps headers, returns validated `EventRecord` models. | Skips invalid rows with structured logging. |
| `event_sync/models.py` | Pydantic models for event rows (date/time validation, registration normalization). | Enables unit tests + safer orchestration. |
| `event_sync/images.py` | Drive download, Pillow-based resizing, Wix upload caching. | Respects 25 MB limit, logs size reductions. |
| `event_sync/orchestrator.py` | High-level flows: validate, test, list, sync. | Uses logging instead of prints, works with typed models. |
| `event_sync/logging_utils.py` | Thin helper to configure package-wide logging. | CLI sets log level globally. |
| `wix_client.py` | REST wrapper with retry logic. | Reused by dev scripts and orchestrator. |
| `dev_events.py` / `dev_tickets.py` | Manual tooling for operators. | Still rely on `WixClient`; RSVP endpoints remain deprecated. |
| `tests/` | Pytest suite covering models + image compression. | Run locally via `make unit` and automatically in CI. |

## Recent Improvements

- 🔁 **Modularisation:** All Sheets/Drive/Wix glue lives inside `event_sync/` and is importable for tests or future tooling.
- 🧾 **Central config:** Single source of truth for required env vars (`AppConfig.ensure_valid()`), replacing scattered `os.getenv()` checks.
- 📋 **Typed events:** `EventRecord` normalises registration types, validates dates/times, and guards against negative capacities.
- 🖼️ **Image hardening:** `prepare_image_for_wix()` compresses oversize photos with Pillow; warnings and successes flow through the logger.
- 🧪 **Automated tests:** `pytest` suite exercises models, image logic, and CLI error paths; CI workflow (`ci.yml`) runs on every push/PR.
- 📣 **Structured logging:** All orchestration output now routes through `logging`, allowing CLI users to raise/lower verbosity.
- 🧩 **CLI hardening:** The top-level CLI now reports failures through the logging system (no bare `print` statements) and returns non-zero exit codes on configuration issues.

## How to Validate Changes

1. `make install-dev`
2. `make unit`
3. (Optional) `python sync_events.py validate --log-level DEBUG`
4. Review CI status (`ci.yml`) on the PR.

Keep this document synced with future architectural changes so operators understand the moving pieces.
