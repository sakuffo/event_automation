# Architecture Overview & Hardening Checklist (2025-10-31)

This supersedes the early duplication audit. The project now ships as a small package (`event_sync/`) with clear module boundaries and automated tests.

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

## Remaining Opportunities

1. **Dev scripts parity:** `dev_events.py` / `dev_tickets.py` still use print-style logging and could adopt shared logging helpers.
2. **End-to-end dry run:** Add a mocked integration test (responses/httpretty) to cover the full sync loop without hitting real services.
3. **Credential scaffolding:** Offer a typed `.env.example` (with comments) generated from `AppConfig` to reduce setup mistakes.
4. **Ticket automation roadmap:** If Wix re-enables RSVP APIs, reintroduce higher-level ticket automation behind feature flags.
5. **Observability:** Consider emitting structured JSON logs or hooking into a lightweight error notifier for production runs.

## How to Validate Changes

1. `make install-dev`
2. `make unit`
3. (Optional) `python sync_events.py validate --log-level DEBUG`
4. Review CI status (`ci.yml`) on the PR.

Keep this document synced with future architectural changes so operators understand the moving pieces.
