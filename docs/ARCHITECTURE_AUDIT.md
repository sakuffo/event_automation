# Architecture Audit (2025-10-31)

This note captures the current structure of the event automation tool prior to refactoring.

## Code Inventory

- `sync_events.py` – 750+ line procedural script. Owns env loading, Google auth/service construction, event parsing, image download + resizing, duplicate checks, Wix writes, CLI dispatch.
- `wix_client.py` – Thin REST wrapper with retry logic and upload helper; consumer code imports directly. Credentials handled implicitly via `load_dotenv()` at import time.
- `dev_events.py`, `dev_tickets.py`, `inspect_tickets.py` – Standalone scripts that construct `WixClient` and perform ad‑hoc operations. Each contains its own CLI plumbing and prints.
- `test_*.py` – Legacy experiment scripts (not wired into CI, only partially exercising code paths).

## Monolithic Pressure Points

1. **Runtime coupling** – `SyncRuntime` instantiates Google + Wix clients, caches downloads, enforces rate limits, and is global to the script.
2. **Stateful globals** – Environment variables read at module import; hard to override for tests or alternate deployments.
3. **No modular boundaries** – Fetching from Sheets, Drive download, Wix upload, and event orchestration all live in one module.
4. **Ad-hoc CLI** – `main()` processes `sys.argv` without reusable parser; dev scripts repeat similar logic.
5. **Testing gaps** – Only manual scripts exist; no unit tests or mocks to exercise data validation, caching, or upload flow.

## Documentation Check

| Doc | Status | Notes |
| --- | --- | --- |
| `README.md` | Mostly accurate | Describes project as a “simple script,” matches current state but will become outdated once modularised. Mentions GitHub Actions schedule that is currently disabled (workflow present but not verified). |
| `docs/CODE_AUDIT.md` | Partially outdated | Still references historic duplication inside `sync_events.py` that was moved into `WixClient` during recent refactor; needs rewrite post-modularisation. |
| `docs/DEV_TOOLS.md` | Mostly accurate | Command list matches Make targets, but assumes monolithic script; add new module entry points once refactor lands. |
| `docs/HISTORY.md` | Accurate | Change log current through latest edit. |

## Immediate Opportunities

- Introduce a small package (e.g., `event_sync/`) with modules for config, data sources, services, and CLI. Re-export minimal surface for backwards compatibility.
- Centralise environment validation (dataclass or Pydantic) and log setup; avoid `load_dotenv()` at import time.
- Add typed event model with validation for dates, registration types, numeric fields, and image metadata.
- Establish unit tests around parsing + image prep, plus a smoke test using mocked HTTP responses.
- Refresh documentation/Make targets after modular split.


