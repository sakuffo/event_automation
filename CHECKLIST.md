# Setup Checklist

Track your progress setting up the Wix Events + Notion sync.

## Prerequisites

- [ ] Python 3.9+ installed
- [ ] Wix website with the Events app (plus a dev copy of the site)
- [ ] Notion workspace
- [ ] Google account (service account for Drive images)
- [ ] GitHub account

## Wix

- [ ] API key created with Wix Events permissions
- [ ] Dev site id noted (`WIX_SITE_ID` **and** `WIX_DEV_SITE_ID`)
- [ ] (Optional) Account id noted for Site Media uploads
- [ ] (Optional) eCommerce Manage Orders scope for tax-by-location

## Notion

- [ ] Integration created, token copied (`ntn_…`)
- [ ] Parent page created and shared with the integration
- [ ] `setup-notion` run; four `NOTION_*_DB_ID` values in `.env`
- [ ] Calendar / Board / "Needs attention" views added by hand
- [ ] Default "New Event" database template created

## Google (images)

- [ ] Drive API enabled, service account + JSON key created
- [ ] Image folder shared with the service account email
- [ ] `GOOGLE_CREDENTIALS` set (one-line JSON)

## Local

- [ ] `./setup.sh` / `setup.bat` / `make setup` run
- [ ] `.env` filled in
- [ ] `python sync_events.py validate` all green
- [ ] `python sync_events.py test` connects
- [ ] `python sync_events.py pull` backfills existing events
- [ ] `make unit` passes

## Automation

- [ ] GitHub repo secrets added (see SETUP.md Part 5)
- [ ] Manual workflow dispatch tested
- [ ] (Optional) Notion button webhook wired to repository_dispatch

## First posting cycle

- [ ] Idea row added in Notion (template + date)
- [ ] `sync --dry-run` previews the create
- [ ] Row flipped to Ready; `sync` publishes it with tickets + categories
- [ ] Wix event verified (image, price, tax at checkout)
- [ ] Row shows Published with Wix Event ID + Synced Hash
