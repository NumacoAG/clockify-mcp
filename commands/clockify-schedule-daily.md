---
description: Create a daily scheduled task that scans my Outlook calendar against Clockify and drafts an email with the missing entries.
---

Create a recurring scheduled task using the `scheduled-tasks` MCP that runs **every weekday at 18:00 Europe/Zurich** with this prompt:

```
Today is the daily Clockify reconciliation sweep. Run the `clockify-gaps` skill
on the range from yesterday 00:00 to right-now. For any meetings missing a
Clockify entry, draft (do NOT send) an Outlook email to the user with subject
"📅 Clockify suggestions for <date range> (N missing)" containing the proposal
table inline. End the email with: "Reply with 'file all' or pick numbers to
file from your next Claude Code session."
```

After creating the schedule, show me the resulting task ID and how to disable it (`mcp__scheduled-tasks__update_scheduled_task` or delete via the scheduled-tasks UI). Also do one dry-run **right now** (without creating the schedule, just run the prompt once) so I can see what the daily email will look like.
