# Technical Documentation

This folder contains technical documentation for the Event Automation project.

## Documentation Files

- [INVARIANTS.md](INVARIANTS.md) — **normative**
  - The full, binding text of the design invariants (status lifecycle, tickets/capacity, hashing, matching, sync direction, …)
  - Each section names the tests that pin it
  - **Read before changing sync/push behavior**; summaries live in [AGENTS.md](../AGENTS.md)
- [NOTION_BACKEND.md](NOTION_BACKEND.md)
  - Notion database schemas, views, and manual setup steps (views/templates the API can't create)
- [DEV_TOOLS.md](DEV_TOOLS.md)
  - Day-to-day CLI usage
  - Sandbox vs production configuration
  - Integrated regression checklist (formerly `FUNCTIONALITY_TEST_PLAN.md`)
- [TICKETING.md](TICKETING.md)
  - REST API vs JavaScript SDK differences
  - Automatic ticket creation controls
  - Troubleshooting and best practices
- [CAPACITY_MIGRATION.md](CAPACITY_MIGRATION.md)
  - The one-time migration that retired the event-level Capacity columns
- [CODE_AUDIT.md](CODE_AUDIT.md) — *historical snapshot (2026-05-27), superseded by the July 2026 Notion-only refactor*
- [HISTORY.md](HISTORY.md)
  - Project timeline and change log

## Quick Reference

1. **Changing sync/push behavior?** [INVARIANTS.md](INVARIANTS.md) first — it is binding.
2. **New to the project?** Start with [AGENTS.md](../AGENTS.md), then [TICKETING.md](TICKETING.md).
3. **Want to develop/test?** Read [DEV_TOOLS.md](DEV_TOOLS.md).
4. **Recent changes?** See [HISTORY.md](HISTORY.md) and `git log`.

## Main Documentation

Return to main project documentation:
- [AGENTS.md](../AGENTS.md) - Canonical agent/developer context (commands, architecture, working agreements)
- [README.md](../README.md) - Project overview
- [SETUP.md](../SETUP.md) - Setup guide
- [CHECKLIST.md](../CHECKLIST.md) - Setup checklist
