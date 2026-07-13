---
name: log-session
description: Estimate the current Claude Code session's duration, summarise what was done, and file it to Clockify as a time entry. Use when the user says "log this session", "log my time", "track this to Clockify", or asks to record the work just completed.
---

# Log this Claude Code session to Clockify

You have access to the `clockify` MCP server. Use it to file a time entry for the work that just happened in this conversation. Be proactive but not annoying.

## Universal rules (binding — never break)

1. **15-minute increments only.** Allowed durations: `15m`, `30m`, `45m`, `1h`, `1h15m`, `1h30m`, `1h45m`, `2h`. No `20m`, `25m`, `40m`, `50m`. Round to the *nearest* 15.
2. **Start times on the quarter hour.** Entry `start` and `end` must land on `HH:00`, `HH:15`, `HH:30`, or `HH:45`. When the user says "log now" or gives an approximate wall-clock time, snap the start *backwards* to the previous quarter hour so their actual start-of-work is captured. End follows automatically: `end = start + duration` (and duration is itself 15-min quantised by rule 1, so end lands on a quarter too).
3. **Single entry ≤ 2 hours.** Strongly prefer ≤ 1h30m. If the work spans longer, **split into two or more entries** with descriptions that reflect the phase (e.g. "design + scoping", then "implementation + tests"). Do not file a 3h entry as one row.
4. **Never overlap with an existing entry on the same customer** — whether the existing entry is on the same project or on a different project under the same client, two overlapping entries on one customer means that customer would be double-billed. Always wrong.
5. **For any other overlap behaviour** (e.g. whether parallel work for *different* customers may overlap in time), defer to the user's organisation policy if one is recorded in `~/.claude/CLAUDE.md`, project-level `CLAUDE.md`, or the user's memory. If no policy is present, default to *no* overlap and ask the user before filing an entry that overlaps anything.

## Flow

1. **Estimate the duration.**
   - If the user gave a duration hint (in the slash-command arguments or the conversation), use that figure, then quantize to 15-minute steps.
   - Otherwise ask: *"Roughly how long did this take? (e.g. 45m, 1h30m)"*. Don't guess silently.
   - If the answer comes back as >2h, immediately propose splitting.

2. **Pick the project.**
   - Call `list_projects` (cached, fast).
   - Match by file paths edited, repo name, topic, attendees mentioned in the conversation.
   - If two projects could fit, propose the most likely and ask.
   - Do not log against a project you can't match confidently.
   - **Lazy mapping-sync note**: if the project you chose isn't in the user's CLAUDE.md cheat-sheet (or there's no cheat-sheet at all), add a single one-liner to the *end* of your final response: *"FYI: '`<project>`' wasn't in your CLAUDE.md cheat-sheet. Run `/clockify-sync-mappings` to add a rule for it."* Do not interrupt the flow, do not edit the user's CLAUDE.md from this skill — that's `/clockify-sync-mappings`'s job, and it always asks before writing.

3. **Write a 1-sentence description.** Concrete, timesheet-readable a month from now. ✅ "Refactored the auth middleware and added two regression tests". ❌ "worked on stuff" / "coded".

4. **Propose a time range.**
   - Default: end = now, start = end − quantized duration.
   - Round both start and end to the nearest 5 min for readability (the *duration* is what must be quantized to 15).
   - Honour explicit hints ("this morning", "yesterday afternoon").

5. **Dedup-check** (mandatory). Call `list_time_entries(start=today_00:00, end=now, project_id=<chosen>)`. If you find an existing entry on the same project overlapping the proposed slot → block, show the user, ask what to do. If you find an entry on a different project at the same time, consult the user's overlap policy from memory/CLAUDE.md; if no policy is present, surface the overlap and ask.

6. **Confirm with a one-liner**:
   *"About to log: 1h30m on <project> (today 14:00–15:30) — '<description>'. OK?"*

7. **Post** via `add_time_entry`. Display the returned entry id so the user can `update_time_entry` / `delete_time_entry` if needed.

## Don'ts

- Don't post without confirmation.
- Don't invent the duration.
- Don't log against a project you couldn't match confidently.
- Don't include a list of files changed in the description.
- Don't break the 15-min quantum.
- Don't file an entry that overlaps another on the same customer.

## Useful tools

- `whoami` — sanity-check on first use.
- `list_projects(name_filter="...")` — narrow when the user gives a hint.
- `list_time_entries(start, end, project_id)` — dedup check / show what's been logged today.
- `report_summary(start, end, only_me=True)` — answer "how much have I worked this week?".
- `update_time_entry(entry_id, …)` / `delete_time_entry(entry_id)` — undo / fix.
