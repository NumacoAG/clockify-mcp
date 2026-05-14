---
description: Scan my Outlook calendar against my Clockify entries and propose what's missing. Defaults to the last 7 days; pass a range as an argument.
allowed-tools:
  - mcp__clockify-mcp__clockify__list_projects
  - mcp__clockify-mcp__clockify__list_time_entries
  - mcp__clockify-mcp__clockify__add_time_entry
  - mcp__d6d04096-1c07-4776-aa02-1fae04f9921e__outlook_calendar_search
---

Use the `clockify-gaps` skill to reconcile my Outlook calendar with my Clockify entries.

Range: $ARGUMENTS (default: last 7 days, ending today)

Follow the skill's playbook exactly: pull calendar + entries + projects in parallel, classify each event, quantize proposals to 15-min increments, never propose > 2h as one entry, never double-book the same customer, present a table, wait for approval, then post.
