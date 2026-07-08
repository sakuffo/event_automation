# Notion Backend

The event pipeline is backed by four Notion databases. People enter event
placeholders directly in Notion; the Python CLI enriches them from the
template catalog and pushes them to Wix. Every row carries a status so anyone
can see what's posted, what's pending, and what failed.

The legacy Google Sheets pipeline still exists side by side (commands suffixed
`-sheet`, plus `prepare-sheet` / `pull-config` / `push-config` /
`pull-categories` / `push-categories`) and will be removed once the Notion
path has proven itself through a full posting cycle.

## The databases

Created by `python sync_events.py setup-notion` under the parent page
(`NOTION_PARENT_PAGE_ID`). IDs live in `.env` / GitHub secrets.

### Event Scheduling — the single source of truth

Formerly the "Events" DB (env var `NOTION_EVENT_SCHEDULING_DB_ID`; the old
`NOTION_EVENTS_DB_ID` is still accepted). One row per event — everything
scheduled, plus what's being ideated on and yet to be scheduled. Replaces the
`rolling_schedule`, `generated_events`, `config_events`, and
`category_config` sheet tabs.

| Property | Type | Who writes it | Notes |
|---|---|---|---|
| Name | title | humans | Event title; also the template-name match key for enrichment |
| Status | select | humans + sync | `Idea` → `Draft` → `Ready` → `Published`; `Update` → `Published`; `Cancel` → `Cancelled`; `Delete` → `Removed`; plus `Error` and `Skip` |
| Date | date (with time) | humans | Start and end datetime in one property |
| Template | relation → Catalog | humans (optional) | Explicit link to a catalog template (class or event); otherwise enrich matches by Name |
| Categories | multi-select | humans + enrich | Wix category tags; enrich adds template tags (+ `rope`/`class` baseline for class templates only) |
| Location | rich text | enrich (default) or humans | |
| Registration Type | select | enrich (default TICKETS) or humans | TICKETS / RSVP / EXTERNAL / NO_REGISTRATION |
| Capacity | number | enrich or humans | Total sellable tickets (inventory), **not** a per-order limit |
| Ticket Price | number | enrich (pricing table → `default_ticket_price`) or humans | Single-ticket price; an explicit `0` creates a free ticket. Ticketed rows never stay blank — the global `default_ticket_price` (seeded 30) is the last-resort fill |
| Ticket Names / Ticket Prices / Ticket Capacities | rich text | enrich (template ticket defaults) or humans | Semicolon-separated for multi-ticket events (`Regular; Student`); each capacity is that ticket type's inventory (blank entries inherit the row Capacity). Enrich fills blanks from the template's `Default Ticket Names/Prices/Capacities` |
| Ticket Limit Per Order | number | enrich (default 4) or humans | Max tickets one buyer can purchase in a single checkout (1–50). Blank = Wix's own default of 20. Event-level Wix setting — the per-ticket `limitPerCheckout` is read-only in the Wix API |
| Checkout Form | select | enrich (from `default_checkout_form`) or humans | `PER_TICKET` = every attendee fills their own registration form; `PER_ORDER` = one form per checkout. Blank = not managed (the Wix dashboard setting is left alone). Maps to Wix `guestsAssignedSeparately` |
| Fee Type, Sale Start, Sale End | rich text | humans (optional) | Ticket definition extras |
| Tax Name / Tax Rate / Tax Type | rich text / number / rich text | enrich (HST 13) or humans | Per-event ticket tax |
| Instructor / Model | rich text | humans | Prepended to the description as "Instructors: …" |
| Teaser | rich text | enrich (class tagline) or humans | Wix short description |
| Description | rich text | enrich (class description) or humans | Long text is chunked into 2000-char segments automatically |
| Image URL | url | enrich (class image → default_img) or humans | Google Drive link or any http(s) image URL |
| Wix Event ID | rich text | **code only** | Set after create/pull; the update match key |
| Last Synced | date | **code only** | |
| Synced Hash | rich text | **code only** | Hash of the last-synced payload (either direction); drives change detection |
| Sync Error | rich text | **code only** | Why a row failed or what's missing; empty = healthy |
| Ticket Policy Status | rich text | **code only** | Do the live tickets carry the Settings `default_ticket_policy`? Blank = policy off or no tickets; `OK (3 tickets)` = all match; `2 of 3 tickets missing policy` = drift (flip to Update, or wait for the next sync, to converge) |
| Source | select | **code only** | `manual` / `wix` / `gcal` (future Google Calendar importer) |
| External Ref | rich text | **code only** | Reserved for external importers |

### Catalog — the template library (classes *and* recurring events)

Formerly the "Classes" DB (env var `NOTION_CATALOG_DB_ID`; the old
`NOTION_CLASSES_DB_ID` is still accepted). Replaces the `class_info` tab,
generalized beyond classes: one row per standardized template. `Type`
(select) says what kind of template a row is:

- `class` — the original class catalog (blank Type reads as `class`, so rows
  created before the redesign are unaffected). Enrich merges the template
  categories **plus the `rope`/`class` baseline tags** and guarantees a price
  (Price Override → `CATEGORY_PRICING` → $30).
- `event` — recurring non-class events (jams, parties, shows, socials like
  Tinker Tuesday or Voyeur). Enrich uses exactly the template's categories
  (no baseline tags) and only prices from `Price Override` (a $0 override is
  honored — free events stay free; no override means a human sets the price).

Shared columns: `Template` (title; the Name-match key), `Categories`,
`Tagline`, `Description`, `Image URL`, optional `Price Override`,
`Default Capacity`, `Default Start Time` / `Default End Time` (HH:MM, e.g.
`19:00` — applied when a row's Date has no time, so a date-only Date is
enough), `Default Instructor` (applied when a row's Instructor is blank;
it lands in the description as "Instructors: …"), and
`Default Ticket Names` / `Default Ticket Prices` / `Default Ticket
Capacities` (semicolon-separated like the Event Scheduling columns; filled
onto blank ticketed rows so a template can define its full ticket lineup —
e.g. `Regular; Student` / `35; 25` / `20; 4`. Template ticket defaults beat
the global `default_ticket_price`; prices/capacities only apply when ticket
names end up on the row, since the trio is keyed by names).

Seeded via `import-classes` (classes, from the old sheet) and
`import-event-templates` (event templates, from the annotated events export
CSV); maintained in Notion from then on.

### Settings — key/value defaults

Replaces the `defaults` tab, and holds the pipeline defaults (seeded by
`setup-notion`, editable in Notion — no code change needed to adjust them):

| Key | Seeded value | Used for |
|---|---|---|
| `default_img` | (from the old sheet) | Fallback event image when the class has none |
| `default_location` | 1233R Queen St W, Toronto | Location for rows without one |
| `default_capacity` | 24 | Capacity for rows without one |
| `default_registration_type` | TICKETS | Registration Type for rows without one |
| `default_tax_name` | HST | Ticket tax on TICKETS events |
| `default_tax_rate` | 13 | Percent (13 = 13%) |
| `default_tax_type` | ADDED_AT_CHECKOUT | Or INCLUDED_IN_PRICE |
| `default_fee_type` | FEE_ADDED_AT_CHECKOUT | Wix service fee handling on tickets |
| `default_ticket_limit_per_order` | 4 | Max tickets per checkout on TICKETS events (1–50; Wix's own default is 20 when a row is left blank) |
| `default_ticket_price` | 30 | Last-resort single-ticket price for TICKETS rows still blank after the template/category pricing chain — a priceless TICKETING event would publish with no tickets at all ("Tickets are not on sale") |
| `default_checkout_form` | (blank) | `PER_TICKET` (each ticket needs its own registration form) or `PER_ORDER` (one form per checkout) for TICKETS rows without a Checkout Form value. Blank = not managed — rows stay blank and the Wix dashboard setting wins |
| `default_duration_hours` | 2 | End time = start + this many hours when a row has no end time (template Default End Time wins when set) |
| `default_ticket_policy` | (blank) | Policy blurb printed on every ticket of every event (Wix `policyText`, max 1000 chars) — e.g. the insurance notice that must accompany each ticket sold. Blank = off. Applied to every ticket the pipeline creates, and converged onto existing tickets when an event is diffed (Ready-match or Update flip); run `scripts/apply_ticket_policy.py --apply` to backfill events already live in Wix |

### Site Config — tax by location

Replaces the `site_config` tab. One row per eCommerce manual tax mapping.
Editable: `Tax Name`, `Tax Type`, `Tax Rate` (percent, e.g. `13`). Read-only
matching keys: `Region ID`, `Group ID`, `Mapping ID`, `Revision`.

## The lifecycle

```
Idea ──enrich──▶ Draft ──human review, flip to──▶ Ready ──sync──▶ Published ◀──sync refreshes from Wix
                                                            │         │
                                                        (failure)   flip to Update ──sync pushes to Wix──▶ Published
                                                            ▼         │
                                                          Error     flip to Cancel ──sync──▶ Cancelled
                                                                      │                          │
                                                                    flip to Delete ──sync──▶ Removed
                                                                     (from any state)
```

- **Idea** — a bare placeholder. A linked Template (or a Name matching one)
  plus a Date is enough — the Date doesn't even need a time if the template
  carries Default Start/End Times, and even the Status can be left blank
  (enrich bootstraps it to Idea). `enrich` copies the template's name into a
  blank Name, fills times and instructor from the template defaults, fills
  the rest, and promotes the row to Draft. If required fields are still
  missing, the row stays put and `Sync Error` says what's missing.
- **Draft** — enriched, awaiting human review. Edit anything. `enrich` never
  overwrites a non-empty field.
- **Ready** — a human approved it; the next `sync` creates it in Wix (event +
  tickets + categories + image), writes back the Wix Event ID, and marks it
  Published. Rows flipped straight to Ready (skipping enrich) get the same
  default fill at sync time, written back to the row.
- **Published** — live on Wix, and **Wix is now the source of truth**: each
  `sync` refreshes the Notion row from the live event, so edits made on the
  website (or by other tools) flow back into Notion. Local edits to a
  Published row are overwritten on the next sync — flip the row to Update
  first if the Notion edit is the one that should win. An event cancelled on
  the website flips its row to Cancelled.
- **Update** — a human edited the row and wants those local changes pushed to
  Wix (the reverse of the Published refresh). The next `sync` diffs the row
  against the live event, patches Wix, and lands the row back on
  **Published**. If Wix already matches, the row just returns to Published.
- **Cancel** — a human wants the event cancelled. The next `sync` calls the
  Wix cancel API (closes registration; Wix sends cancellation notifications to
  registrants if that's enabled on the site) and marks the row **Cancelled**.
  Wix can't un-cancel an event — to reuse it, duplicate the row (without the
  Wix Event ID), fix the details, and set it Ready.
- **Cancelled** — cancelled on Wix but still listed there. `pull` also maps
  events cancelled from the Wix dashboard onto their rows as Cancelled.
- **Delete** — a human wants the event gone from Wix entirely. The next
  `sync` deletes it via the Wix API (works from Cancelled or any other state,
  even rows with sold tickets — cancel first if registrants should be
  notified) and marks the row **Removed**. If the event is already gone, the
  row is simply marked Removed.
- **Removed** — deleted from Wix. The row stays in Notion as the historical
  record; the pipeline ignores it from here on.
- **Error** — something failed; the reason is in `Sync Error`. Fix and set
  back to Ready (or Update for an already-published event) and re-sync.
- **Skip** — parked; the pipeline ignores it (holidays, cancelled ideas).

## Commands

```bash
python sync_events.py setup-notion       # one-time: create the 4 databases (re-run to patch schemas)
python sync_events.py import-classes     # one-time: class_info sheet -> Catalog DB
python sync_events.py import-event-templates --dry-run   # preview recurring-event baselines
python sync_events.py import-event-templates             # seed Type=event templates from the export CSV
python sync_events.py pull               # Wix -> Event Scheduling DB (backfill/refresh Published rows)
python sync_events.py pull --scope all   # include past events

python sync_events.py enrich             # fill blanks on Idea/Draft rows (preview/debug; sync does this too)
python sync_events.py enrich -m aug sep  # only touch specific months

python sync_events.py sync --dry-run     # preview what would change (skips the enrich pass)
python sync_events.py sync               # enrich pass, push Ready/Update rows, refresh Published rows from Wix
python sync_events.py sync --no-enrich   # push without the enrich pass
python sync_events.py sync --draft       # create new events as Wix drafts
python sync_events.py sync -m aug        # only sync specific months

python sync_events.py pull-site-config   # Wix tax mappings -> Site Config DB
python sync_events.py push-site-config --dry-run
python sync_events.py push-site-config   # apply tax rates to Wix
```

Notes:

- `import-event-templates` reads an events export CSV (produced by
  `scripts/export_events_csv.py`, then annotated by hand with `default_event`
  — the recurring-family name — and `include_in_feed` TRUE/FALSE columns).
  For each family it takes the **latest feed-eligible instance** as the
  baseline; Tinker Tuesday additionally skips "Sunday" specials and requires
  the $25 base price (the $28.25 siblings have HST baked in). Families whose
  name already exists in the catalog are skipped unless `--force` is passed.
- `pull` never overwrites rows in Idea/Draft/Ready/Update/Error/Skip status.
  If a pulled Wix event matches such a row by title+date+time, the row is
  *linked* (Wix Event ID written) but its fields are left alone.
- `sync` starts with the same enrich pass as the `enrich` command (skip with
  `--no-enrich`; dry runs skip it automatically since enrich writes to
  Notion). Drafts still need a human flip to Ready before anything is pushed.
- `sync` matches rows to Wix by `Wix Event ID` first, then by
  title+date+time — so a hand-added Ready row that duplicates an existing Wix
  event updates it instead of creating a duplicate.
- If a Ready row matches a Wix draft, `sync` publishes the draft (unless
  `--draft` is passed).

## Default "New Event" template (create by hand — the API can't create templates)

So new rows come pre-filled with the boring fields, set up a default database
template once (about 3 minutes):

1. Open the **Event Scheduling** database → click the dropdown arrow next to the blue
   `New` button → `+ New template`.
2. Name the template `New Event` and set these properties on it:
   - Status: `Idea`
   - Registration Type: `TICKETS`
   - Location: `1233R Queen St W, Toronto, ON M6K 1L5, Canada`
   - Capacity: `24`
   - Ticket Limit Per Order: `4`
   - Tax Name: `HST`, Tax Rate: `13`, Tax Type: `ADDED_AT_CHECKOUT`
   - Fee Type: `FEE_ADDED_AT_CHECKOUT`
3. Back in the template dropdown, hover over `New Event` → `⋯` →
   `Set as default` → `For all views in Events`.

From then on, clicking `New` gives a row where you only link the Template and
pick the Date (the Name fills itself from the template at enrich time — type
one only to override it). If someone skips the template (or a value), the
pipeline still fills every blank from Settings at `enrich` time — and as a
safety net at `sync` time for rows flipped straight to `Ready`.

## Recommended views (create by hand — the API can't create views)

- **Calendar** on the Date property — the planning view.
- **Board** grouped by Status — what's posted / pending / failed at a glance.
- **Needs attention** — table filtered to `Sync Error` is not empty.
- **This month / Next month** — table filtered on Date for the posting cycle.

## Triggering runs

Runs are plain CLI invocations — locally or via GitHub Actions
([.github/workflows/sync-events.yml](../.github/workflows/sync-events.yml)):

- **Daily cron** runs `sync` (which starts with an enrich pass) at 9 AM EST.
- **Manual**: Actions tab → "Sync Events from Notion to Wix" → Run workflow.
- **From Notion** (optional, paid plan): a database automation or button with
  a *Send webhook* action can fire the workflow instantly:
  - URL: `https://api.github.com/repos/{owner}/{repo}/dispatches`
  - Headers: `Authorization: Bearer <GitHub PAT with repo scope>`,
    `Accept: application/vnd.github.v3+json`
  - Body: `{"event_type": "notion-sync"}`

  Nothing depends on this — it's a convenience trigger for the same run.

## Environment variables

```bash
NOTION_ACCESS_TOKEN=ntn_...              # integration token (share the parent page with it)
NOTION_PARENT_PAGE_ID=...                # page that holds the databases (setup-notion only)
NOTION_EVENT_SCHEDULING_DB_ID=...        # was NOTION_EVENTS_DB_ID (old name still accepted)
NOTION_CATALOG_DB_ID=...                 # was NOTION_CLASSES_DB_ID (old name still accepted)
NOTION_SETTINGS_DB_ID=...
NOTION_SITE_CONFIG_DB_ID=...
```

Wix credentials are unchanged (`WIX_API_KEY`, `WIX_SITE_ID`, …).
`GOOGLE_CREDENTIALS` is still required for Google Drive image downloads;
`GOOGLE_SHEET_ID`/`SOURCE_SHEET_ID` are only needed by the legacy `-sheet`
commands and `import-classes`.
