---
description: File a Clockify time entry for the work just done in this Claude Code session. Follows quantization + overlap rules.
allowed-tools:
  - mcp__clockify-mcp__clockify__list_projects
  - mcp__clockify-mcp__clockify__list_time_entries
  - mcp__clockify-mcp__clockify__add_time_entry
  - mcp__clockify-mcp__clockify__whoami
---

Use the `log-session` skill to propose and file a Clockify entry for what we just worked on.

Optional duration hint: $ARGUMENTS (e.g. `45m`, `1h30m`; otherwise estimate from the conversation or ask me).

Follow the skill rules strictly: 15-min increments only, single entry ≤ 2h (prefer ≤ 1h30m), never overlap on the same customer, always confirm before posting.
