---
name: clockify-gaps
description: Find meetings in the user's Outlook calendar that don't have a matching Clockify time entry, and propose entries to fill the gaps. Use when the user says "scan my calendar", "what haven't I logged", "find missing Clockify entries", "reconcile my calendar with Clockify". Also invoked by the scheduled daily sweep.
---

# Find and propose missing Clockify entries

You have access to the `clockify` MCP and the Outlook (Microsoft 365) MCP. Cross-reference the user's calendar with their Clockify entries; surface the meetings that should be billed but aren't, with concrete proposed entries.

## Universal rules (binding — same as `log-session`)

1. **15-minute increments only.** `15m`, `30m`, `45m`, `1h`, `1h15m`, `1h30m`, `1h45m`, `2h`. Round to the nearest 15.
2. **Single entry ≤ 2 h.** Prefer ≤ 1h30m. If a calendar event ran > 2 h, split into chunks with descriptions reflecting each phase.
3. **Never overlap with an existing entry on the same customer** — whether on the same project or a different project under the same client. That's double-billing the customer, always wrong.
4. **For any other overlap behaviour** (e.g. parallel work for *different* customers), defer to the user's organisation policy if one is recorded in `~/.claude/CLAUDE.md`, project-level `CLAUDE.md`, or memory. If no policy is present, default to *no* overlap and ask the user before proposing an entry that would overlap anything.
5. **Cancelled meetings → skip** (`isCancelled: true`).
6. **All-day events → skip** unless the user confirms they were full work days on a single project.
7. **`free` showAs → skip** (the user wasn't actually busy).
8. **Internal company syncs → skip by default** (all-hands, ops syncs, sales syncs, 1:1s with the user's own team). The user can include them on request. If their CLAUDE.md/memory lists specific internal-only meeting patterns to skip, honour that.

## Flow

1. **Pick the range.** If unspecified, default to the last 7 days. The user often says "yesterday" / "last week" / "this month" — interpret accordingly.

2. **Pull data in parallel:**
   - `outlook_calendar_search(query="*", afterDateTime=start, beforeDateTime=end, limit=50)` — calendar events.
   - `list_time_entries(start, end)` — existing Clockify entries for the same range.
   - `list_projects()` — to match meeting subjects/attendees to project IDs (cached).

3. **Classify each calendar event:**
   - **Skip** if cancelled, all-day with showAs=free, or recognisably internal-to-the-user's-company.
   - **Match to a project** by subject keywords, attendee email domains, organiser email. Use the project list from `list_projects()` as the source of truth — match calendar signals against project names and any client names they expose. If the user's memory/CLAUDE.md contains a mapping cheat-sheet for their workflow, honour it.
   - Anything not matchable → flag as "needs human classification".

4. **Cross-check against existing entries:**
   - Existing entry on same project overlapping the event → `covered ✓`.
   - Otherwise → `proposed`, subject to the user's overlap policy.

5. **Quantize proposals:**
   - Duration = event end − event start, rounded to nearest 15 min.
   - If > 2 h, split into chunks of ≤ 1h30m each, with descriptions reflecting natural breakpoints (`"phase 1 — design"`, `"phase 2 — implementation"`).
   - Compose description: `"<short event summary>, with <key attendees>"`. Translate cryptic meeting subjects into action verbs.

6. **Present a table.** Columns: When (local tz, 15-min slots), Duration, Project, Proposed description, Source (calendar event subject), Note (tentative / split / ambiguous).

7. **Flag ambiguities loudly.** If a meeting could match two projects, ask which.

8. **Ask for approval** before posting. Accept "file all" / "file #N, #M" / "skip #N" / "edit #N to <duration>".

9. **Post via `add_time_entry`** — one parallel batch. Show IDs after.

10. **Lazy mapping-sync note.** After the post step, compare the projects you ended up using against the user's CLAUDE.md cheat-sheet. If any project is in Clockify but absent from the cheat-sheet (or no cheat-sheet exists), add a single one-liner at the end of your response: *"FYI: {N} Clockify project(s) without a mapping rule. Run `/clockify-sync-mappings` to add rules."* Never modify the user's CLAUDE.md from this skill — `/clockify-sync-mappings` owns that and always asks before writing.

## When the scheduled daily sweep invokes you

The agent prompt asks you to scan **yesterday** and **today** and either:

- **Draft (do not send) an email** to the user via `create_draft` on the M365 MCP with the proposal table inline, OR
- **Append to a pending-suggestions file** that the next Claude Code session surfaces.

Default to drafting an email if M365 is connected. Subject: `📅 Clockify suggestions for <date range> (N entries, M missing)`. End with the magic phrase *"reply with 'file all' or pick numbers to file"* so the next conversation can act on it.

## Useful tools

- `outlook_calendar_search(query, afterDateTime, beforeDateTime, limit, showAs?)` — calendar.
- `list_time_entries(start, end, project_id?)` — Clockify entries.
- `list_projects(name_filter?)` — projects (cached).
- `report_summary(start, end, only_me=true, group_by="DAY")` — sanity-check day-by-day totals.
- `add_time_entry(start, end, project_id, description)` — post the approved entries.

## Don'ts

- Don't auto-file. Always show the table and ask.
- Don't propose entries on internal company syncs unless the user explicitly opts in.
- Don't propose 20m / 50m durations. **Quantize.**
- Don't propose > 2 h as one entry. **Split.**
- Don't propose an entry that overlaps an existing one on the same customer.
