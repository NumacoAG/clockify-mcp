---
description: Reconcile your Clockify project list with the project-mapping cheat-sheet in ~/.claude/CLAUDE.md. Surfaces any project without a mapping rule and helps you add one (always asks before writing).
allowed-tools:
  - mcp__clockify-mcp__clockify__list_projects
---

Sync the project-mapping cheat-sheet in `~/.claude/CLAUDE.md` with the user's current Clockify projects.

## Workflow

1. **Read the cheat-sheet.** Open `~/.claude/CLAUDE.md`. Find the section titled *"Project mapping cheat-sheet"* (or however the user named it — look for headings/bullets that map domains/keywords to project names). If no cheat-sheet section exists at all, report that and offer to bootstrap one.

2. **Extract currently-mapped project names** from the cheat-sheet (the right-hand side of each bullet — typically after `→` or `->`).

3. **Pull the live Clockify list.** Call `list_projects(include_archived=False)`. Build a set of `(name, client_name)` tuples.

4. **Diff.** Compute the projects present in Clockify but absent from the cheat-sheet's right-hand side. Match by exact project name. If unsure (e.g., a Clockify project name is a substring of a cheat-sheet entry), flag as "possibly mapped, please confirm" rather than treating as new.

5. **If everything is mapped**, report:
   > *"✓ All N Clockify projects have mapping rules. Nothing to sync."*
   …and exit.

6. **Otherwise, walk the unmapped projects one at a time.** For each:
   - Show the user: project name, client name, color, billable, current logged hours (call `report_summary` if helpful).
   - Ask: *"What attendee domains or subject keywords should map to `<project name>`? (e.g. `@acme.com, keywords Mexico, La Candelaria`. Reply 'skip' to leave it unmapped for now.)"*
   - Compose a draft bullet matching the existing cheat-sheet style. For example:
     ```
     - @acme.com, keywords Mexico / La Candelaria → New Project Name.
     ```

7. **Binding rule — always confirm before writing.**
   - Show the user the **exact text of the bullet** you propose to append, plus the **exact location** (which section, which subsection) inside `~/.claude/CLAUDE.md`.
   - Wait for an explicit confirmation: *"yes"* / *"ok"* / *"go"*. Anything else = don't write.
   - If the user wants to edit the draft, accept the edits and re-show before writing.

8. **Apply the edit** using your file-edit tools. Append the bullet to the *Project mapping cheat-sheet* subsection. Preserve existing indentation, ordering, and any sibling bullets.

9. **After all are processed**, show a summary:
   - Bullets added (N)
   - Projects skipped (the user said 'skip')
   - Possibly-mapped flagged (the user should review manually)

## Don'ts

- **Don't write to `CLAUDE.md` without showing the proposed text first and getting an explicit yes.** This is binding.
- Don't invent keywords. If the user can't think of any, mark the project skipped (not mapped to a generic default).
- Don't touch sections of `CLAUDE.md` outside the Clockify cheat-sheet. Read carefully, edit narrowly.
- Don't remove existing mappings even if the project is now archived in Clockify. Surface it for the user, let them decide.

## Tools at hand

- `list_projects(include_archived=False)` — current project list, includes client names and estimates.
- `report_summary(start, end, project_ids=[id], only_me=True)` — handy if the user asks "how much have I logged on this new project so far?"
- Built-in `Read` / `Edit` — for `~/.claude/CLAUDE.md`.
