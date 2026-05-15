---
name: log-session
description: Estimate the current Claude Code session's duration, summarise what was done, and file it to Clockify as a time entry. Use when the user says "log this session", "log my time", "track this to Clockify", or asks to record the work just completed. Also fires when the Stop hook nudges you to consider suggesting a time entry.
---

# Log this Claude Code session to Clockify

You have access to the `clockify` MCP server. Use it to file a time entry for the work that just happened in this conversation. Be proactive but not annoying.

## Universal rules (binding — never break)

1. **15-minute increments only.** Allowed durations: `15m`, `30m`, `45m`, `1h`, `1h15m`, `1h30m`, `1h45m`, `2h`. No `20m`, `25m`, `40m`, `50m`. Round to the *nearest* 15.
2. **Single entry ≤ 2 hours.** Strongly prefer ≤ 1h30m. If the work spans longer, **split into two or more entries** with descriptions that reflect the phase (e.g. "design + scoping", then "implementation + tests"). Do not file a 3h entry as one row.
3. **Never overlap with an existing entry on the same customer** — whether the existing entry is on the same project or on a different project under the same client, two overlapping entries on one customer means that customer would be double-billed. Always wrong.
4. **For any other overlap behaviour** (e.g. whether parallel work for *different* customers may overlap in time), defer to the user's organisation policy if one is recorded in `~/.claude/CLAUDE.md`, project-level `CLAUDE.md`, or the user's memory. If no policy is present, default to *no* overlap and ask the user before filing an entry that overlaps anything.

## Flow

1. **Estimate the duration.**
   - If a `<system-reminder>` (from the Stop hook) tells you how long the session has been going, use that figure, then quantize to 15-minute steps.
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

## When the Stop hook nudges you

The nudge looks like:

> *⏱ Clockify nudge — session running ~N min (quantize to Xh). ACTION: at the END of your next user-facing response, append a separator and then exactly one line in this shape: 'By the way, we've been at this for ~Xh — want me to log it on `<inferred project>` as "<one-sentence summary>"? (yes / no / different)'. ...*

**The nudge is an instruction, not a suggestion.** Don't wait for a "natural pause" — in an active conversation there isn't one. Follow this exact pattern:

1. **Answer the user's current message fully.** Whatever they asked, address it normally. Don't skip your real answer to talk about logging.
2. **At the end of your response**, add a separator (a blank line is fine).
3. **On the next line, write exactly one line** in this shape — nothing more:

   > *"By the way, we've been at this for ~`<duration>` — want me to log it on `<inferred project>` as `'<one-sentence summary>'`? (yes / no / different)"*

4. **Stop there.** Don't propose a time range, don't pre-dedup, don't list options. Just the one line.
5. **If the user replies "yes" / "no" / "different" on the next turn**, then fall into the full Flow above (dedup-check via `list_time_entries`, propose start/end, confirm one-liner, post via `add_time_entry`).

### When to skip the nudge

**The only valid skip:** you've already surfaced a Clockify log proposal earlier in THIS conversation.

**Tool calls are NOT a skip reason.** If your response has tool calls, do them first, then put the proposal line at the very end of the same response, after all narration. The model is allowed to interleave text and tool calls in one turn — use that. Tool calls and the trailing proposal line co-exist in the same response.

**"Mid-task" is NOT a valid skip reason.** Every conversation feels mid-task — the user has always just asked for the next thing. The nudge exists precisely to puncture that perception. If you find yourself thinking *"I'll wait for the right moment"*, the right moment is now, and you must surface this turn.

**Sub-agent context** (you're invoked by another agent and your output goes back to a parent, not the user) is a valid skip — there's no user to read the proposal.

### Inferring the project and summary

- **Project**: scan the conversation for repo names, file paths edited, attendees mentioned, customer keywords. Match against `list_projects()` if you haven't already this session.
- **Summary**: one sentence, action verb first ("Implemented", "Fixed", "Reviewed", "Drafted"). Concrete enough to be useful a month later.
- If you can't confidently infer a project, propose the most likely and let the user correct in their reply.

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
