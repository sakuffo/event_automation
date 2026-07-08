# Setup Guide — Wix Events + Notion Sync

Step-by-step setup for the Notion-backed event pipeline. Total time:
roughly 30–45 minutes, most of it collecting credentials.

## Prerequisites

- Python 3.9+
- A Wix website with the Events app installed (plus a **dev copy** of the
  site for testing)
- A Notion workspace where the event databases will live
- A Google Cloud service account (only for Drive-hosted event images)
- A GitHub account for the daily automation

## Part 1: Wix API key (5 minutes)

1. Go to <https://manage.wix.com/account/api-keys> and create an API key.
2. Give it **Wix Events** permissions (and eCommerce **Manage Orders** if you
   plan to use the tax-by-location site config).
3. Note the API key, your **dev** site id, and (optional, for image uploads)
   your account id.

> ⚠️ Point `WIX_SITE_ID` at the **dev copy** of the site while testing.
> Production only gets configured once the flow is proven.

## Part 2: Notion integration (10 minutes)

1. Go to <https://www.notion.so/my-integrations> and create an internal
   integration; copy the token (`ntn_…`).
2. In Notion, create (or pick) a page that will hold the event databases.
3. Share that page with the integration (page menu → Connections).
4. Copy the page id from its URL into `NOTION_PARENT_PAGE_ID`.

## Part 3: Google service account (10 minutes — images only)

Event images are Drive-hosted; the pipeline downloads them via a service
account.

1. In Google Cloud Console, create a project (or reuse one) and enable the
   **Google Drive API**.
2. Create a service account, then a JSON key for it.
3. Share the Drive folder holding event images with the service account's
   email (viewer access).
4. Put the whole JSON (one line) into `GOOGLE_CREDENTIALS`.

## Part 4: Local setup (5 minutes)

```bash
./setup.sh        # or setup.bat on Windows, or: make setup
# fills .env from .env.example — edit it with the values from Parts 1–3

python sync_events.py validate     # every ✅ should be green
python sync_events.py test         # confirms the Wix connection
```

Then bootstrap the Notion databases:

```bash
python sync_events.py setup-notion
# copy the four printed NOTION_*_DB_ID values into .env

python sync_events.py import-event-templates   # optional: seed recurring-event templates
python sync_events.py pull                     # backfill rows from live Wix events
```

Finally, in Notion (by hand — the API can't create these):

- Calendar view on **Date**, Board view grouped by **Status**
- A "Needs attention" table filtered to non-empty **Sync Error**
- The default "New Event" database template
  (see [docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md))

## Part 5: GitHub Actions (5 minutes)

Add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `WIX_API_KEY` | from Part 1 |
| `WIX_SITE_ID` | the site the automation should target |
| `WIX_ACCOUNT_ID` | optional (Site Media) |
| `NOTION_ACCESS_TOKEN` | from Part 2 |
| `NOTION_EVENT_SCHEDULING_DB_ID` | printed by setup-notion |
| `NOTION_CATALOG_DB_ID` | printed by setup-notion |
| `NOTION_SETTINGS_DB_ID` | printed by setup-notion |
| `NOTION_SITE_CONFIG_DB_ID` | printed by setup-notion |
| `GOOGLE_CREDENTIALS` | from Part 3 |

[.github/workflows/sync-events.yml](.github/workflows/sync-events.yml) then
runs `sync` daily, on manual dispatch, and on the `notion-sync`
repository-dispatch webhook. Runs are serialized by a concurrency group.

## Part 6: Dev safety

- `WIX_DEV_SITE_ID` in `.env` declares which site is the dev site; the
  destructive `scripts/dev` commands (`delete-*`) refuse to run unless
  `WIX_SITE_ID` matches it.
- `pytest` / `make unit` never touch live APIs — collection is confined to
  `tests/`, and the manual scripts live in `scripts/dev/`.

## Troubleshooting

- **validate fails on NOTION_*_DB_ID** — run `setup-notion` and copy the
  printed ids into `.env`.
- **Notion pages not found** — share the parent page with the integration
  (Connections menu), then re-run.
- **Images not uploading** — confirm the Drive folder is shared with the
  service account email and `GOOGLE_CREDENTIALS` is valid one-line JSON.
- Add `--log-level DEBUG` to any command for full API traces.
