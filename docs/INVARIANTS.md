# Invariants (normative)

This document is **binding for AI agents and humans alike**: it is the full, normative text of the design invariants summarized in [AGENTS.md](../AGENTS.md). Each section names the tests that pin it — the tests, not this prose, are the enforcement, so **change behavior only with a matching test change**, and update this file in the same commit.

The text below was moved verbatim from CLAUDE.md's "Key Design Patterns" section during the August 2026 context-engineering restructure; treat it with the same authority.

## Status lifecycle

Events rows move `Idea → Draft → Ready → Published` (plus `Update`, `Error`, `Skip`). `enrich` fills only *empty* fields (from Template relation/name-match, Settings `default_*` rows with constants fallback, `CATEGORY_PRICING`) and promotes Idea→Draft; humans flip Draft→Ready; the explicit `push` flips Ready→Published (and Update→Published after pushing local edits) and records failures as Error with the reason in `Sync Error`. The scheduled `sync` never writes to Wix: it runs a pull pass (`pull_events`, upcoming scope; skip with `--no-pull`) and the enrich pass first (`--no-enrich` skips it; dry runs skip both automatically because they write to Notion), then refreshes Published rows — Ready/Update/Cancel/Delete rows are collected into a `pending_push` summary bucket telling the human to run `push`. Rows with a linked Template but a blank Name get the template's name written in by enrich before default-fill, and a blank Status is bootstrapped to Idea (the enrich fetch includes status-less rows via `include_missing_status`) — picking a template plus a date is enough to start a row. Templates also carry `Default Start Time`/`Default End Time` (HH:MM rich text; applied to the row's Date when its time parts are blank, rewriting the Date property) and `Default Instructor` (applied when the row's Instructor is blank, feeding the "Instructors: …" description prefix). A row with a start but no usable end (missing or equal to start) gets `default_duration_hours` (Settings, seeded 2) added; an end at/before the start on the same day is read as overnight and the end date rolls forward. `EventRecord` rejects end<=start outright ("End must be after start …") so zero-duration rows fail with a clear Sync Error instead of a Wix 400.

*Pinned by `tests/test_sync_loop.py` (status-lifecycle characterization) and `tests/test_sync_enrich_pass.py`.*

## Catalog template types

The Catalog DB (title property `Template`; Events link to it via the `Template` relation) has a `Type` select distinguishing `class` templates (blank = `class` for pre-redesign rows) from recurring `event` templates. In `_apply_row_defaults`, only class templates get the `rope`/`class` baseline tags and the guaranteed price (Price Override → `CATEGORY_PRICING` → $30); event templates contribute exactly their own categories and price only from `Price Override` (a $0 override is honored for free events). `setup-notion` patches the Type select into an existing Catalog DB via `ensure_template_type_options` and performs the one-time renames via `migrate_naming`; `import-event-templates` seeds event templates from the annotated events-export CSV (latest feed-eligible instance per `default_event` family — see `TEMPLATE_SOURCE_RULES`).

*Pinned by `tests/test_event_templates.py` and `tests/test_defaults.py`.*

## Guaranteed ticket defaults (Template → Settings → Constants)

Every ticketed row ends up with creatable tickets. There is **no event-level Capacity column** — per-ticket inventory (Wix `initialLimit`) lives solely in the semicolon `Ticket Capacities` column. Templates carry `Default Ticket Names/Prices/Capacities` (semicolon rich text mirroring the row columns); `_fill_tickets` copies them onto blank ticketed rows before `_fill_pricing`. Names fill first and prices only apply when the row ends up with names (a price list without names would produce no tickets), but **capacities stand alone**: every ticketed row is guaranteed a `Ticket Capacities` value (template list → `default_capacity` Setting, seeded 24, `DEFAULT_CAPACITY` constant fallback). In `parse_tickets`, missing tail entries inherit the *last provided* value (like prices), so a single value covers every ticket type; capacities must be **positive numbers** (decimals round to whole tickets) — blank/zero/negative/unparseable entries fall back to the `default_capacity` Setting, which reaches the create paths via `runtime.get_default_ticket_capacity()` (resolved once per run, mirroring `get_ticket_policy_text`; non-positive Setting values are rejected with a warning). Unlimited tickets are dashboard-only: the pipeline never creates one and the update plan never touches an unlimited live ticket. The single-ticket path (`ensure_ticket_definition`) caps with the first value via `single_ticket_capacity`. `compute_event_update_plan` parses the identical spec as creation, and capacities are **managed per entry** in the Update diff (`managed_ticket_capacities`): only an explicit positive token — or the single-value tail inheritance — is diffed; blank/invalid tokens, like a fully blank column, leave that live ticket alone (the ticket-limit convention), so an Update flip can never silently shrink live inventory. `_fill_pricing` gained a last-resort fallback: a still-priceless ticketed row gets the `default_ticket_price` Setting (seeded 30, `DEFAULT_TICKET_PRICE` constant fallback), so the old silent no-ticket publish can't happen (order: Price Override → `CATEGORY_PRICING` → class $30 floor → global default). `event_property_for_field` routes a semicolon `ticket_price` back to the `Ticket Prices` rich text (single value → `Ticket Price` number) and can write `ticket_name`/`ticket_capacity`. In `ensure_event_tickets`/`_repair_missing_tickets`, an explicit `0` price (`has_explicit_zero_price`: single-value `ticket_price_raw` parsing to 0) creates a **free ticket** (Wix accepts `fixedPrice` "0", verified on the dev site) — only a genuinely blank price creates nothing. Ticket-creation failures surface as Sync Error: `runtime.last_ticket_failure` (mirrors `last_image_failure`) is set by `create_wix_event`, and `_create_new_event` / the publish-draft and matched-update branches of `_push_matched_ready_row` write "Published but ticket creation failed …" with a blank `Ticket Policy Status`.

The retired `Capacity`/`Default Capacity` number columns were migrated by `scripts/migrate_capacity_columns.py` (see `docs/CAPACITY_MIGRATION.md`).

*Pinned by `tests/test_defaults.py` (fills), `tests/test_ticket_capacity.py` (inventory invariants), and `tests/test_sync_loop.py` (free/blank branch + failure notes).*

## Cancel/Delete actions

Humans flip a row to `Cancel` (push calls `WixClient.cancel_event`, row becomes `Cancelled`) or `Delete` (push calls `delete_event(force=True)`, row becomes `Removed`). These branches run *before* record validation in `notion_push_events` — an incomplete row can still be cancelled/deleted; matching only needs the Wix Event ID (or title+date+time). Wix drafts can't be cancelled (use Delete); Wix can't un-cancel (duplicate the row without the Wix Event ID to recreate). `pull` maps Wix `CANCELED` events to `Cancelled` rows, and `setup-notion` patches missing Status select options into an existing Events DB via `ensure_event_status_options`.

*Pinned by `tests/test_sync_loop.py` (Cancel/Delete act before validation).*

## Settings defaults and `_apply_row_defaults`

`setup-notion` seeds `default_location`/`default_capacity` (the fallback *ticket* capacity — see [Guaranteed ticket defaults](#guaranteed-ticket-defaults-template--settings--constants))/`default_registration_type`/`default_tax_*`/`default_fee_type`/`default_ticket_limit_per_order`/`default_ticket_price`/`default_checkout_form`/`default_duration_hours`/`default_ticket_policy` rows into the Settings DB (edit there, not in code). `fetch_settings` keeps blank-valued rows (as `""`) so deliberately-blank seeds aren't re-created every setup run. The shared `_apply_row_defaults` helper (in `notion_orchestrator.py`) is used by `enrich` on Idea/Draft rows and by `push` as a safety net on Ready rows (with write-back), so Notion always shows exactly what was pushed. A manual default "New Event" database template gives creation-time defaults in the UI (documented in `docs/NOTION_BACKEND.md`; templates can't be created via API).

*Pinned by `tests/test_defaults.py`.*

## Global ticket policy blurb

The Settings row `default_ticket_policy` (seeded blank; max 1000 chars) is the ticket definition's `policyText` — the policy printed on every ticket a buyer receives (insurance requirement). It is *not* a row field: `SyncRuntime.get_ticket_policy_text()` resolves it once per run and every ticket-creation path passes it through (`ensure_event_tickets` → `create_tickets_from_config`/`ensure_ticket_definition`), while `compute_event_update_plan` diffs `policyText` on all existing ticket defs and patches drift. A blank setting means "not managed" — nothing sent, dashboard-written policies untouched (same semantics as blank `Ticket Limit Per Order`). `scripts/apply_ticket_policy.py` (dry-run by default, `--apply` to write) backfills tickets already live in Wix.

*Pinned by `tests/test_ticket_policy.py`.*

## Ticket Policy Status (read-only column)

Code-owned rich text on Event Scheduling showing whether the live tickets carry the policy — blank (policy off / no tickets), `OK (n tickets)`, or drift like `2 of 3 tickets missing policy`. Wording is owned by `wix_mapping.ticket_policy_status`; `_wix_event_to_record` computes it from the ticket defs already fetched (zero extra API calls) for the Published refresh and `pull`, and push handlers stamp the expected value via `_expected_policy_status`. It is bookkeeping (never hashed), so the hash fast-paths treat a stale value as `stale_bookkeeping` — drift alone still triggers the write.

*Pinned by `tests/test_ticket_policy.py`.*

## Sales columns are pull-only (Tickets Sold / Tickets Sold By Type / Revenue)

Code-owned columns on Event Scheduling refreshed from live Wix sales data by `sync`'s Published refresh and by `pull` — **never by humans, never toward Wix**. `Tickets Sold` (number) and `Tickets Sold By Type` (semicolon list, positionally aligned with `Ticket Names` because both come from the same ticket-definition order) sum `salesDetails.soldCount` from the ticket-definitions fetch the refresh already makes (`include_sales=True` requests the `SALES_DETAILS` fieldset); `Revenue` comes from the read-only `GET /events/v1/orders/summary` endpoint (confirmed orders; one extra read per ticketed event, skipped for events without ticket definitions). The wording/values are owned by `wix_mapping.ticket_sales_summary` and `wix_mapping.order_summary_revenue`. Pull-only is structural: the fields are bookkeeping on `EventRecord` (never in `HASHED_FIELDS`, so sales drift never flips a row toward push), no push path reads them, and `build_wix_event_payload`/`diff_event_fields` never reference them — there is no Wix write these columns can cause. Like `Ticket Policy Status`, sales drift alone counts as `stale_bookkeeping` on the hash fast-paths (a ticket purchase becomes visible without a full row rewrite), and a failed order-summary call yields `None` = "leave the column alone" — existing values are never blanked by an API hiccup. Human edits to these columns are simply overwritten on the next refresh.

*Pinned by `tests/test_sales_dashboard.py`.*

## Events Dashboard page (generated, display-only)

`sync` finishes by rewriting one Notion page (`notion_dashboard.refresh_dashboard`): an upcoming-events table (non-historical dates whose `Hidden from Schedule` checkbox is clear; sold / capacity / % with the old GitHub Pages dashboard's 70%/100% color thresholds) and a by-month sales summary over every dated Published row, including hidden history. Both are built purely from Published rows (`build_dashboard_blocks`, pure function). The page is identified by the auto-managed `dashboard_page_id` Setting — blank means "create under `NOTION_PARENT_PAGE_ID` next sync and remember it"; a deleted page is recreated once. Display-only and contained: nothing on the page feeds back into sync/push, building it never talks to Wix, a dashboard failure never fails the sync, and dry runs skip it entirely.

*Pinned by `tests/test_sales_dashboard.py` (block builder) and `tests/test_sync_direction.py` (fetch pattern).*

## Checkout Form

`Checkout Form` select (`PER_TICKET` / `PER_ORDER`) on Event Scheduling maps to the event-level Wix `registration.tickets.guestsAssignedSeparately` boolean (PER_TICKET = each ticket needs its own registration form). Modeled exactly like `Ticket Limit Per Order`: blank = not managed (nothing sent on create, never diffed, dashboard setting untouched); enrich fills blank ticketed rows from the `default_checkout_form` Setting (seeded blank); pull/Published refresh read the live value back (Wix omits false booleans — a non-empty tickets object reads as `PER_ORDER`). `checkout_form` is a hashed `EventRecord` field, so its introduction re-writes each Published row's hash once.

*Pinned by `tests/test_checkout_form.py`.*

## Sync direction by status

Once a row is `Published`, **Wix is authoritative** — each operational (not hidden) row is refreshed by `sync` from the live event (via `wix_event_to_config_row` + `upsert_event_from_record`, like `pull`; a row matching a Wix `CANCELED` event flips to `Cancelled`, and Wix events too incomplete to validate land with a `Sync Error` note). Published rows outside the current schedule window retain their last snapshot and skip ticket/sales reads until they become current again. To push local Notion edits to Wix instead, humans flip the row to `Update` and run `push`: it diffs the row against Wix (`compute_event_update_plan`, no hash fast-path — an explicit Update always diffs), applies the plan, and lands the row back on `Published` (no changes needed → straight back to `Published`). The Published refresh needs no record validation (an incomplete Notion row can still be refreshed); Update rows that fail validation become `Error`.

*Pinned by `tests/test_sync_direction.py`.*

## Image preservation

A failed image upload at create time still creates the event but writes a `Sync Error` note (via `runtime.last_image_failure`) telling the editor to fix the link and flip to Update. The Published refresh and `pull` never blank a human-entered (non-wixstatic) Image URL just because the Wix event has no image — the preserved URL is applied *before* hashing so the short-circuit stays consistent. Wixstatic row URLs are code-written, so an image deliberately removed on the website stays removed.

*Pinned by `tests/test_sync_loop.py` (image preservation).*

## Hash-based change detection

After each successful push (and each Published refresh), `EventRecord.content_hash()` is stored in `Synced Hash`. Published rows skip the Notion write when the row already matches the Wix-derived record's hash. The hash canonicalizes formatting (`35.0`≡`35`, None≡"") and hashes `ticket_price_raw` over the derived `ticket_price`. Empty semicolon tokens are kept positionally (`20; ; 4` ≢ `20; 4`) — an unlimited live ticket reads back as an empty capacity slot, and collapsing it would hide dashboard capacity edits from the refresh forever. `Hidden from Schedule` is bookkeeping and is never hashed or sent to Wix.

*Pinned by `tests/test_notion_store.py` (hash stability).*

## Matching

Rows match Wix by `Wix Event ID` first, falling back to `(title, start_date, start_time)` — key format owned by `wix_mapping.event_match_key`. A Ready row matching a Wix draft gets *published*; matching a live event gets *updated/linked* — never duplicated. GitHub Actions runs are serialized via a `concurrency` group so overlapping syncs can't race their Notion writes (pull-created rows / Published refreshes doubling up).

*Pinned by `tests/test_sync_loop.py` (Ready-matches-draft publishes; never duplicate).*

## Pull is non-destructive

`pull` creates/refreshes only `Published`/`Cancelled` (code-owned) rows; rows in any human status (including `Update`) are linked (Wix ID written) but their content fields are never overwritten. The code-owned `Hidden from Schedule` flag may be cleared when a human status needs attention. Wix events too incomplete to validate still land in Notion with a `Sync Error` note (`upsert_event_from_raw_row`).

*Pinned by `tests/test_sync_loop.py` (pull links-but-never-overwrites human rows).*

## Schedule visibility and recurring series

`Hidden from Schedule` is a code-owned checkbox that removes stale rows from operational Event Scheduling views without archiving them. `wix_mapping.select_schedule_wix_event_ids` is the single owner of the selection: only Wix `UPCOMING`/`STARTED` events are current; ordinary Wix-native recurring series retain only the occurrence whose `dateAndTimeSettings.recurrenceStatus` is `RECURRING_UPCOMING`; exact case-insensitive title `Tinker Tuesday` is the deliberate exception and retains the earliest four current occurrences, sorted by Wix start timestamp then ID. A `STARTED` Tinker occurrence consumes one of the four slots.

Published rows whose Wix ID is outside that set, plus `Cancelled`/`Removed` rows, are checked hidden and retained as history. Human workflow statuses are kept visible. Hidden Published rows skip content/ticket/sales refreshes, but remain in the complete Wix ID/key indexes: push matching is never visibility-filtered, so Update/Cancel/Delete actions cannot create duplicates. As an occurrence ends, the next Tinker row is unhidden on the next pull/sync. `pull --scope all` still backfills non-Tinker history as hidden, but does not create extra Tinker rows outside the four-event window. `scripts/archive_recurring_rows.py` excludes Tinker rows from its optional cleanup.

The generated dashboard's Upcoming section excludes checked rows; its by-month summary still includes them. Operational Notion views filter to unchecked, while a History view filters to checked.

*Pinned by `tests/test_sync_loop.py`, `tests/test_sync_direction.py`, `tests/test_notion_store.py`, and `tests/test_sales_dashboard.py`.*

## Notion property conventions

`Registration Type` select shows `TICKETS` (mapped to Wix `TICKETING` by the model validator); multi-ticket fields stay semicolon-separated text (`Ticket Names/Prices/Capacities` — capacities are per-type *inventory*, Wix `initialLimit`); `Ticket Limit Per Order` (number, 1–50) is the max tickets per checkout — the event-level Wix `registration.tickets.ticketLimitPerOrder` (blank defers to Wix's default of 20; enrich fills ticketed rows from `default_ticket_limit_per_order`, seeded 4; the per-ticket-definition `limitPerCheckout` is read-only in the Wix API so this event-level knob is the only one); single price lives in the `Ticket Price` number property; long descriptions are chunked/rejoined across 2000-char rich_text segments; select options must not contain commas (`_sanitize_option`).

*Pinned by `tests/test_notion_store.py` (property round-trips) and `tests/test_ticket_limit.py`.*

## Timezones

Notion dates are written as naive local datetimes with `time_zone: America/Toronto`; reads convert UTC-offset datetimes back to local via `zoneinfo` (`tzdata` dependency on Windows).

## Site-config round-trip (tax by location)

Rows live in the Site Config DB; only `tax_name`/`tax_type`/`tax_rate` (percent) are editable; push updates/bulk-creates mappings, never deletes; the row-processing core is `wix_flows.process_site_config_rows`. Requires the eCommerce **Manage Orders** scope.

## Views are one-time workspace configuration

The runtime Notion client manages data-source schemas and rows, not database views. Operational views must filter `Hidden from Schedule` to unchecked, with a separate checked History view (documented in `docs/NOTION_BACKEND.md`).
